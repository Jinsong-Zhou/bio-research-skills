"""Source adapters: query construction, silent-failure guards, parsing.

Everything here is offline except the tests marked ``live``, which check that
the API's real behaviour still matches what ``references/source-quirks.md``
documents. Run those with ``uv run pytest -m live``.
"""

from datetime import date, timedelta
from xml.etree import ElementTree as ET

import pytest
from sources import arxiv, biorxiv, europepmc, pubmed

# arXiv's HTTP-200 error document, verbatim in shape.
ARXIV_ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format</id>
    <title>Error</title>
    <summary>incorrect id format for cat</summary>
  </entry>
</feed>"""

ARXIV_ONE_PAPER = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2601.01234v2</id>
    <published>2026-01-05T18:00:00Z</published>
    <updated>2026-02-11T09:30:00Z</updated>
    <title>A folding
      study</title>
    <summary>We fold things.</summary>
    <author><name>Wei Zhang</name></author>
    <author><name>Ada Okafor</name></author>
    <link href="http://arxiv.org/pdf/2601.01234v2" type="application/pdf" title="pdf"/>
    <category term="q-bio.BM"/>
    <arxiv:doi>10.1234/example</arxiv:doi>
  </entry>
</feed>"""


class TestArxivQuery:
    def test_structured_operators_are_not_wrapped_in_a_field_prefix(self):
        """Wrapping in all: is what makes arXiv return its 200-with-Error doc."""
        query = arxiv.build_query(
            keywords=["cryo-EM"],
            categories=["q-bio.BM"],
            since=date(2026, 7, 20),
            until=date(2026, 7, 30),
        )
        assert query.startswith("(cat:q-bio.BM)")
        assert "all:cat:" not in query
        assert "submittedDate:[202607200000 TO 202607302359]" in query

    def test_multiword_keywords_are_quoted(self):
        query = arxiv.build_query(keywords=["protein folding", "cryoEM"])
        assert 'all:"protein folding"' in query
        assert "all:cryoEM" in query

    def test_groups_are_ored_within_and_anded_between(self):
        query = arxiv.build_query(keywords=["a", "b"], categories=["q-bio.BM", "q-bio.QM"])
        assert query == "(cat:q-bio.BM OR cat:q-bio.QM) AND (all:a OR all:b)"

    def test_unbounded_queries_are_refused(self):
        with pytest.raises(ValueError, match="unbounded"):
            arxiv.build_query()


class TestArxivParsing:
    def test_the_error_document_raises_instead_of_looking_empty(self):
        with pytest.raises(arxiv.ArxivQueryError, match="incorrect id format"):
            arxiv._check_for_error_entry(ET.fromstring(ARXIV_ERROR_FEED))

    def test_a_real_single_result_feed_is_not_mistaken_for_an_error(self):
        arxiv._check_for_error_entry(ET.fromstring(ARXIV_ONE_PAPER))  # must not raise

    def test_entry_parsing(self):
        paper = arxiv._parse_entry(ET.fromstring(ARXIV_ONE_PAPER).find("{*}entry"))
        assert paper.paper_id == "2601.01234", "version suffix should be stripped"
        assert paper.extra["arxiv_id_versioned"] == "2601.01234v2"
        assert paper.title == "A folding study", "wrapped titles should be reflowed"
        assert paper.authors == ["Wei Zhang", "Ada Okafor"]
        assert paper.published_date == date(2026, 1, 5)
        assert paper.updated_date == date(2026, 2, 11)
        assert paper.doi == "10.1234/example"
        assert paper.pdf_url.endswith(".pdf") or "pdf" in paper.pdf_url


class TestBiorxivCategories:
    @pytest.mark.parametrize(
        "given", ["cell biology", "Cell Biology", "CELL_BIOLOGY", "  cell_biology  "]
    )
    def test_matching_ignores_case_and_separators(self, given):
        assert biorxiv.resolve_category(given, "biorxiv") == "cell_biology"

    def test_a_keyword_passed_as_a_category_is_refused(self):
        """Unvalidated, the API drops the filter and returns unrelated papers."""
        with pytest.raises(biorxiv.UnknownCategoryError, match="silently ignore"):
            biorxiv.resolve_category("protein folding", "biorxiv")

    def test_near_misses_get_a_suggestion(self):
        with pytest.raises(biorxiv.UnknownCategoryError, match="Did you mean.*neuroscience"):
            biorxiv.resolve_category("neuroscienc", "biorxiv")

    def test_servers_have_separate_vocabularies(self):
        assert biorxiv.resolve_category("cardiovascular medicine", "medrxiv")
        with pytest.raises(biorxiv.UnknownCategoryError):
            biorxiv.resolve_category("cardiovascular medicine", "biorxiv")

    def test_a_bad_category_fails_before_any_network_call(self, monkeypatch):
        monkeypatch.setattr(
            biorxiv, "fetch_json", lambda *a, **k: pytest.fail("validated too late")
        )
        with pytest.raises(biorxiv.UnknownCategoryError):
            biorxiv.search(since=date(2026, 7, 1), categories=["not a real area"])


def biorxiv_item(**overrides):
    """A record shaped like one element of the API's ``collection``."""
    return {
        "doi": "10.64898/x",
        "title": "T",
        "authors": "A",
        "abstract": "",
        "date": "2026-07-01",
        "version": "1",
        "category": "biochemistry",
        **overrides,
    }


class TestBiorxivRecords:
    def test_version_collapsing_keeps_the_newest(self):
        items = [
            biorxiv_item(version="1", date="2026-07-01"),
            biorxiv_item(version="3", date="2026-07-20"),
        ]
        papers = biorxiv._collapse_versions([biorxiv._to_paper(i, "biorxiv") for i in items])
        assert len(papers) == 1
        assert papers[0].extra["version"] == "3"

    def test_the_literal_string_NA_is_not_treated_as_a_doi(self):
        item = biorxiv_item(published="NA")
        assert biorxiv._to_paper(item, "biorxiv").extra["published_doi"] == ""

    def test_a_published_doi_is_carried_through_for_dedup(self):
        journal_doi = "10.1016/j.cell.2026.01.001"
        item = biorxiv_item(published=journal_doi)
        assert biorxiv._to_paper(item, "biorxiv").extra["published_doi"] == journal_doi

    def test_an_unknown_server_is_rejected(self):
        with pytest.raises(ValueError, match="server must be"):
            biorxiv.search(since=date(2026, 7, 1), server="arxiv")


class TestBiorxivPagination:
    """The API pages oldest-first at a page size it never promises.

    Assuming a constant page size caps every query at one page; reading page 1
    returns the oldest slice, which is backwards for a "what's new" query.
    """

    def _fake_api(self, total, page=30, monkeypatch=None):
        """Serve `total` records, ascending by day, `page` at a time."""
        calls = []

        def fetch(url, params=None, **kwargs):
            cursor = int(url.rstrip("/").rsplit("/", 1)[-1])
            calls.append(cursor)
            batch = [
                biorxiv_item(
                    doi=f"10.64898/{i:04d}",
                    date=f"2026-07-{1 + i // 50:02d}",
                    title=f"Paper number {i:04d}",
                )
                for i in range(cursor, min(cursor + page, total))
            ]
            return {"messages": [{"total": str(total), "count": len(batch)}], "collection": batch}

        monkeypatch.setattr(biorxiv, "fetch_json", fetch)
        return calls

    def test_more_than_one_page_is_fetched(self, monkeypatch):
        """30 < an assumed PAGE_SIZE of 100 would break after the first page."""
        calls = self._fake_api(300, page=30, monkeypatch=monkeypatch)
        papers = biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8), max_results=90)
        assert len(papers) == 90
        assert len(calls) > 2, "a single page means the loop exited early"

    def test_the_newest_records_are_returned_not_the_oldest(self, monkeypatch):
        self._fake_api(300, page=30, monkeypatch=monkeypatch)
        result = biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8), max_results=30)
        returned = {int(p.doi.split("/")[-1]) for p in result.papers}
        assert min(returned) >= 270, f"got the oldest slice: {sorted(returned)[:3]}"

    def test_the_windows_true_size_is_reported_so_truncation_is_visible(self, monkeypatch):
        self._fake_api(300, page=30, monkeypatch=monkeypatch)
        result = biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8), max_results=30)
        assert result.available == 300
        assert result.truncated is True, "30 of 300 must not look like a complete sweep"

    def test_the_cursor_advances_by_the_real_batch_length(self, monkeypatch):
        """Advancing by an assumed page size skips records between pages."""
        calls = self._fake_api(200, page=30, monkeypatch=monkeypatch)
        biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8), max_results=120)
        steps = [b - a for a, b in zip(calls[1:], calls[2:])]
        assert all(step == 30 for step in steps), f"cursor jumped: {calls}"

    def test_a_window_smaller_than_one_page_needs_no_seeking(self, monkeypatch):
        calls = self._fake_api(12, page=30, monkeypatch=monkeypatch)
        papers = biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8), max_results=200)
        assert len(papers) == 12
        assert calls == [0], "one page held everything; no second request needed"

    def test_categories_get_equal_budgets(self, monkeypatch):
        """Otherwise the first, busiest category consumes the whole allowance."""
        self._fake_api(300, page=30, monkeypatch=monkeypatch)
        papers = biorxiv.search(
            since=date(2026, 7, 1),
            until=date(2026, 7, 8),
            categories=["biochemistry", "biophysics"],
            max_results=60,
        )
        # Both passes hit the same stub, so version collapsing folds them back
        # into one set of 30 — the point is that neither asked for all 60.
        assert len(papers) == 30

    def test_an_empty_window_is_not_an_error(self, monkeypatch):
        self._fake_api(0, monkeypatch=monkeypatch)
        result = biorxiv.search(since=date(2026, 7, 1), until=date(2026, 7, 8))
        assert result.papers == []
        assert result.truncated is False


class TestPubmedDates:
    """Year-only PubDate is the trap: it lands every record on 1 January."""

    def _article(self, body):
        return ET.fromstring(f"<PubmedArticle>{body}</PubmedArticle>")

    def test_electronic_article_date_is_preferred(self):
        article = self._article(
            "<MedlineCitation><Article>"
            "<ArticleDate><Year>2026</Year><Month>07</Month><Day>14</Day></ArticleDate>"
            "<Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>"
            "</Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) == date(2026, 7, 14)

    def test_entrez_history_is_the_fallback(self):
        article = self._article(
            "<MedlineCitation><Article>"
            "<Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>"
            "</Article></MedlineCitation>"
            '<PubmedData><History><PubMedPubDate PubStatus="entrez">'
            "<Year>2026</Year><Month>7</Month><Day>3</Day>"
            "</PubMedPubDate></History></PubmedData>"
        )
        assert pubmed._best_date(article) == date(2026, 7, 3)

    def test_abbreviated_month_names_parse(self):
        article = self._article(
            "<MedlineCitation><Article><Journal><JournalIssue>"
            "<PubDate><Year>2026</Year><Month>Jul</Month><Day>9</Day></PubDate>"
            "</JournalIssue></Journal></Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) == date(2026, 7, 9)

    def test_year_only_falls_back_to_january_first(self):
        article = self._article(
            "<MedlineCitation><Article><Journal><JournalIssue>"
            "<PubDate><Year>2026</Year></PubDate>"
            "</JournalIssue></Journal></Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) == date(2026, 1, 1)

    def test_an_impossible_day_is_clamped_not_dropped(self):
        article = self._article(
            "<MedlineCitation><Article>"
            "<ArticleDate><Year>2026</Year><Month>2</Month><Day>31</Day></ArticleDate>"
            "</Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) == date(2026, 2, 1)

    def test_a_medline_date_range_yields_nothing_rather_than_a_wrong_day(self):
        article = self._article(
            "<MedlineCitation><Article><Journal><JournalIssue>"
            "<PubDate><MedlineDate>2026 Jul-Aug</MedlineDate></PubDate>"
            "</JournalIssue></Journal></Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) is None


class TestPubmedArticles:
    def test_structured_abstracts_keep_their_labels(self):
        article = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>"
            "<ArticleTitle>A study</ArticleTitle>"
            '<Abstract><AbstractText Label="BACKGROUND">Context.</AbstractText>'
            '<AbstractText Label="RESULTS">Findings.</AbstractText></Abstract>'
            "<AuthorList><Author><LastName>Zhang</LastName><Initials>W</Initials></Author>"
            "</AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        paper = pubmed._parse_article(article)
        assert paper is not None
        assert paper.abstract == "BACKGROUND: Context. RESULTS: Findings."
        assert paper.authors == ["Zhang W"]
        assert paper.url.endswith("/12345/")

    def test_a_record_without_a_pmid_or_title_is_dropped(self):
        assert pubmed._parse_article(ET.fromstring("<PubmedArticle/>")) is None

    def test_consortium_authors_survive(self):
        article = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle><AuthorList>"
            "<Author><CollectiveName>The Structural Genomics Consortium</CollectiveName></Author>"
            "</AuthorList></Article></MedlineCitation></PubmedArticle>"
        )
        assert pubmed._parse_article(article).authors == ["The Structural Genomics Consortium"]

    def test_keywords_and_term_are_mutually_optional(self):
        with pytest.raises(ValueError, match="keywords or a raw PubMed term"):
            pubmed.search(since=date(2026, 7, 1))

    def test_the_entrez_date_is_recorded_alongside_the_publication_date(self):
        """esearch filters on the Entrez date; the digest shows the pub date.

        Keeping only one makes a record indexed in July but published in April
        look like it violates a seven-day window.
        """
        article = ET.fromstring(
            "<PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
            "<ArticleTitle>T</ArticleTitle>"
            "<ArticleDate><Year>2026</Year><Month>4</Month><Day>13</Day></ArticleDate>"
            "</Article></MedlineCitation>"
            '<PubmedData><History><PubMedPubDate PubStatus="entrez">'
            "<Year>2026</Year><Month>7</Month><Day>28</Day>"
            "</PubMedPubDate></History></PubmedData></PubmedArticle>"
        )
        paper = pubmed._parse_article(article)
        assert paper.published_date == date(2026, 4, 13)
        assert paper.extra["entrez_date"] == "2026-07-28"


class TestEuropePmcQuery:
    def test_the_query_is_scoped_to_preprints_and_the_window(self):
        query = europepmc.build_query(
            ["cryo-EM", "membrane transporter"], date(2026, 7, 23), date(2026, 7, 30)
        )
        assert query.startswith('(SRC:"PPR")')
        assert 'PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv"' in query
        assert "FIRST_PDATE:[2026-07-23 TO 2026-07-30]" in query

    @pytest.mark.parametrize(
        ("keyword", "expected"),
        [
            ("CRISPR (Cas9)", '"CRISPR Cas9"'),
            ('say "hello"', '"say hello"'),
            ("a:b", '"a b"'),
            ("wild*card", '"wild card"'),
        ],
    )
    def test_query_syntax_is_stripped_from_keywords(self, keyword, expected):
        """Unbalanced syntax makes Europe PMC drop clauses and answer 200 anyway."""
        assert europepmc._escape(keyword) == expected

    def test_a_rewritten_query_is_an_error_not_a_result(self):
        payload = {"request": {"queryString": '(SRC:"PPR")'}}
        with pytest.raises(europepmc.EuropePmcQueryError, match="rewrote the query"):
            europepmc._check_query_echo(payload, '(SRC:"PPR") AND ("cryo-EM")')

    def test_matching_echo_passes(self):
        sent = '(SRC:"PPR") AND ("cryo-EM")'
        europepmc._check_query_echo({"request": {"queryString": sent}}, sent)

    def test_keywords_are_required(self):
        with pytest.raises(ValueError, match="needs keywords"):
            europepmc.build_query([], date(2026, 7, 1), date(2026, 7, 8))

    def test_records_are_flagged_as_keyword_matches(self):
        paper = europepmc._to_paper(
            {
                "id": "PPR1276052",
                "doi": "10.64898/2026.07.06.736854",
                "title": "Narrow-beam geometry improves\n  cryo-EM",
                "authorString": "Matinyan S, Filipcik P.",
                "abstractText": "We do things.",
                "firstPublicationDate": "2026-07-08",
                "bookOrReportDetails": {"publisher": "bioRxiv"},
            }
        )
        assert paper.extra["keyword_match"] is True
        assert paper.extra["preprint_server"] == "biorxiv"
        assert paper.title == "Narrow-beam geometry improves cryo-EM"
        assert paper.authors == ["Matinyan S", "Filipcik P"]
        assert paper.published_date == date(2026, 7, 8)
        assert paper.url.endswith("10.64898/2026.07.06.736854")

    def test_authorlist_wins_over_the_flattened_string(self):
        paper = europepmc._to_paper(
            {
                "id": "PPR1",
                "title": "T",
                "authorString": "Ignore M.",
                "authorList": {"author": [{"fullName": "Okafor Ada"}]},
                "firstPublicationDate": "2026-07-08",
            }
        )
        assert paper.authors == ["Okafor Ada"]

    def test_a_repeated_cursor_terminates_paging(self, monkeypatch):
        """nextCursorMark stops advancing at the end; looping on it never exits."""
        calls = []

        def fetch(url, params=None, **kwargs):
            calls.append(params["cursorMark"])
            return {
                "request": {"queryString": params["query"]},
                "resultList": {"result": [{"id": f"PPR{len(calls)}", "title": "T"}]},
                "nextCursorMark": "SAME",
            }

        monkeypatch.setattr(europepmc, "fetch_json", fetch)
        europepmc.search(keywords=["x"], since=date(2026, 7, 1), until=date(2026, 7, 8))
        assert len(calls) == 2, f"paged forever on a static cursor: {calls}"


@pytest.mark.live
class TestLiveBehaviour:
    """Confirms the quirks in references/source-quirks.md are still real."""

    def test_biorxiv_still_ignores_an_unknown_category(self):
        from sources._http import fetch_json

        window = "2026-07-25/2026-07-29"
        valid = fetch_json(
            f"https://api.biorxiv.org/details/biorxiv/{window}/0", {"category": "neuroscience"}
        )["collection"]
        bogus = fetch_json(
            f"https://api.biorxiv.org/details/biorxiv/{window}/0", {"category": "protein_folding"}
        )["collection"]

        assert {i["category"] for i in valid} == {"neuroscience"}
        assert len({i["category"] for i in bogus}) > 1, (
            "bioRxiv started honouring unknown categories — the whitelist guard "
            "may no longer be necessary"
        )

    def test_biorxiv_page_size_and_ordering_are_what_the_code_assumes(self):
        """The regression that shipped in v0.1, caught only against the real API.

        Constructed fixtures cannot catch this: the mock returns whatever page
        size its author assumed, so the test agrees with the bug.
        """
        from sources._http import fetch_json

        window = "https://api.biorxiv.org/details/biorxiv/2026-07-23/2026-07-30"
        probe = fetch_json(f"{window}/0")
        page_size = len(probe["collection"])
        total = int(probe["messages"][0]["total"])

        assert page_size < 100, (
            f"page size is {page_size}; any code assuming 100 stops after page 1"
        )
        assert total > page_size, "window too small to exercise pagination"

        first = sorted(i["date"] for i in probe["collection"])
        last_page = fetch_json(f"{window}/{max(0, total - page_size)}")
        last = sorted(i["date"] for i in last_page["collection"])
        assert first[-1] <= last[0], (
            "records are no longer oldest-first; the tail-seeking in "
            "_fetch_category would now return the wrong end of the window"
        )

    def test_biorxiv_search_returns_the_newest_records(self):
        result = biorxiv.search(
            since=date.today() - timedelta(days=7),
            categories=["neuroscience"],  # busy enough to exceed one page
            max_results=40,
        )
        assert result.papers, "neuroscience should have preprints in any given week"
        assert result.available, "the window's true size should be reported"
        newest = max(p.published_date for p in result.papers if p.published_date)
        assert (date.today() - newest).days <= 3, (
            f"newest record is {newest}; the fetch is returning stale pages"
        )

    def test_arxiv_structured_queries_still_work(self):
        result = arxiv.search(
            categories=["q-bio.BM", "q-bio.QM"],
            since=date.today().replace(day=1),
            max_results=3,
        )
        assert result.papers, "q-bio should have submissions this month"
        assert all(
            any(c.startswith("q-bio") for c in p.categories) for p in result.papers
        )
        assert result.available and result.available >= len(result.papers)

    def test_sampled_categories_stay_within_the_whitelist(self):
        from sources._http import fetch_json

        for server in biorxiv.SERVERS:
            seen = {
                item["category"]
                for item in fetch_json(
                    f"https://api.biorxiv.org/details/{server}/2026-06-01/2026-07-29/0"
                )["collection"]
            }
            unknown = [c for c in seen if biorxiv._normalise(c) not in
                       {biorxiv._normalise(k) for k in biorxiv.CATEGORIES[server]}]
            assert not unknown, f"add to CATEGORIES[{server!r}]: {unknown}"

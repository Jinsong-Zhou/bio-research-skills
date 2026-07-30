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

    def _fake_api(self, total, pages=(30,), monkeypatch=None, report_total=True):
        """Serve `total` records oldest-first, in batches cycling through `pages`.

        The batch size **varies on purpose**. A stub that always returns the
        same number cannot tell ``cursor += len(batch)`` apart from
        ``cursor += <that same number>``, so a hardcoded page size passes — which
        is exactly how a hardcoded 100 shipped against an API that pages at 30,
        with a green suite the whole way. Assertions below read the returned
        sizes back out of this log instead of restating a literal.

        Records the outbound ``params`` too: dropping the category filter is a
        silent failure the API answers with real, unrelated papers.
        """
        calls = []
        served = [0]

        def fetch(url, params=None, **kwargs):
            cursor = int(url.rstrip("/").rsplit("/", 1)[-1])
            size = pages[served[0] % len(pages)]
            served[0] += 1
            batch = [
                biorxiv_item(
                    doi=f"10.64898/{i:04d}",
                    date=f"2026-07-{1 + i // 50:02d}",
                    title=f"Paper number {i:04d}",
                )
                for i in range(cursor, min(cursor + size, total))
            ]
            calls.append({"cursor": cursor, "params": params, "returned": len(batch)})
            messages: dict = {"count": len(batch)}
            if report_total:
                messages["total"] = str(total)
            return {"messages": [messages], "collection": batch}

        monkeypatch.setattr(biorxiv, "fetch_json", fetch)
        return calls

    WINDOW = {"since": date(2026, 7, 1), "until": date(2026, 7, 8)}
    #: Deliberately irregular, and none of them equal to another.
    RAGGED = (30, 17, 30, 8, 25)

    def test_more_than_one_page_is_fetched(self, monkeypatch):
        """30 < an assumed PAGE_SIZE of 100 would break after the first page."""
        calls = self._fake_api(300, pages=self.RAGGED, monkeypatch=monkeypatch)
        papers = biorxiv.search(**self.WINDOW, max_results=90)
        assert len(papers) == 90
        assert len(calls) > 2, "a single page means the loop exited early"

    def test_the_cursor_advances_by_the_real_batch_length(self, monkeypatch):
        """Derived from the stub's own log, so no constant can satisfy it.

        The previous version of this test served a constant 30 and asserted a
        literal 30, which ``cursor += 30`` passed. Mutation-tested: replacing
        ``len(batch)`` with any fixed number now fails here.
        """
        calls = self._fake_api(200, pages=self.RAGGED, monkeypatch=monkeypatch)
        biorxiv.search(**self.WINDOW, max_results=120)

        probe, seek, *rest = calls
        assert probe["cursor"] == 0, "the size probe reads page 0"
        assert seek["cursor"] == 200 - 120, "seek straight to the tail, not one page in"

        walk = [seek, *rest]
        assert len({c["returned"] for c in walk}) > 1, "the stub must vary its page size"
        for previous, following in zip(walk, walk[1:]):
            assert following["cursor"] == previous["cursor"] + previous["returned"], (
                f"cursor jumped: served {previous['returned']} at {previous['cursor']}, "
                f"next request asked for {following['cursor']}"
            )

    def test_the_returned_tail_has_no_gaps_and_no_repeats(self, monkeypatch):
        """Ragged pages must still yield exactly the newest N, contiguously."""
        self._fake_api(200, pages=self.RAGGED, monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW, max_results=60)
        indices = sorted(int(p.doi.split("/")[-1]) for p in result.papers)
        assert indices == list(range(140, 200))

    def test_the_newest_records_are_returned_not_the_oldest(self, monkeypatch):
        self._fake_api(300, pages=self.RAGGED, monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW, max_results=30)
        returned = {int(p.doi.split("/")[-1]) for p in result.papers}
        assert min(returned) >= 270, f"got the oldest slice: {sorted(returned)[:3]}"

    def test_a_window_inside_one_page_still_returns_the_newest(self, monkeypatch):
        """The branch that skips the tail-seek must not skip the tail.

        A limit below the page size is ordinary, not exotic: --max-per-source
        100 spread over four subject areas gives each 25, under the API's 30.
        Slicing the page from the front there returns the oldest records in the
        window while every counter still looks healthy.
        """
        self._fake_api(25, pages=(30,), monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW, max_results=10)
        indices = sorted(int(p.doi.split("/")[-1]) for p in result.papers)
        assert indices == list(range(15, 25)), "took the oldest ten of the window"

    def test_the_windows_true_size_is_reported_so_truncation_is_visible(self, monkeypatch):
        self._fake_api(300, pages=self.RAGGED, monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW, max_results=30)
        assert result.available == 300
        assert result.truncated is True, "30 of 300 must not look like a complete sweep"
        assert result.coverage == "truncated"

    def test_a_window_smaller_than_one_page_needs_no_seeking(self, monkeypatch):
        calls = self._fake_api(12, pages=(30,), monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW, max_results=200)
        assert len(result) == 12
        assert [c["cursor"] for c in calls] == [0], "one page held everything"
        assert result.coverage == "complete"

    def test_a_complete_sweep_is_not_called_truncated_just_because_of_versions(
        self, monkeypatch
    ):
        """The API counts version rows; we return one paper per DOI.

        Comparing the raw count against the collapsed list makes every window
        holding a v2 preprint report truncation on a complete sweep — and a
        warning that fires on healthy runs is one the reader stops believing.
        """
        rows = [
            biorxiv_item(doi="10.64898/aaa", version="1", date="2026-07-02"),
            biorxiv_item(doi="10.64898/aaa", version="2", date="2026-07-05"),
            biorxiv_item(doi="10.64898/bbb", version="1", date="2026-07-03"),
        ]
        monkeypatch.setattr(
            biorxiv,
            "fetch_json",
            lambda url, params=None, **kw: {
                "messages": [{"total": "3", "count": len(rows)}],
                "collection": rows,
            },
        )
        result = biorxiv.search(**self.WINDOW, max_results=200)
        assert len(result) == 2, "two DOIs, three version rows"
        assert result.truncated is False
        assert result.coverage == "complete"

    def test_an_unreported_total_is_unknown_coverage_not_a_finished_sweep(self, monkeypatch):
        """`None` must survive to the caller instead of collapsing into 0.

        With no total we cannot seek, so the walk covers the window from the
        front — and the answer is still the newest slice, never page one.
        """
        self._fake_api(80, pages=(30,), monkeypatch=monkeypatch, report_total=False)
        result = biorxiv.search(**self.WINDOW, max_results=20)
        indices = sorted(int(p.doi.split("/")[-1]) for p in result.papers)
        assert indices == list(range(60, 80)), "fell back to the oldest page"
        assert result.available is None
        assert result.coverage == "unknown"
        assert result.truncated is False, "unknown is not the same claim as truncated"

    def test_the_category_filter_reaches_the_api(self, monkeypatch):
        """Dropping it makes the API return every paper in the window, at 200."""
        calls = self._fake_api(50, pages=(30,), monkeypatch=monkeypatch)
        biorxiv.search(**self.WINDOW, categories=["cell biology"], max_results=10)
        assert calls, "no request was made"
        assert all(c["params"] == {"category": "cell_biology"} for c in calls), calls

    def test_no_category_asks_for_every_subject_area(self, monkeypatch):
        calls = self._fake_api(50, pages=(30,), monkeypatch=monkeypatch)
        biorxiv.search(**self.WINDOW, max_results=10)
        assert all(c["params"] == {"category": None} for c in calls), calls

    def test_categories_get_equal_budgets(self, monkeypatch):
        """Otherwise the first, busiest category consumes the whole allowance."""
        calls = self._fake_api(300, pages=(30,), monkeypatch=monkeypatch)
        biorxiv.search(
            **self.WINDOW,
            categories=["biochemistry", "biophysics"],
            max_results=60,
        )
        asked = [c["params"]["category"] for c in calls]
        assert set(asked) == {"biochemistry", "biophysics"}
        # Each pass seeks to total - 30, never total - 60.
        seeks = {c["params"]["category"]: c["cursor"] for c in calls if c["cursor"] > 0}
        assert set(seeks.values()) == {270}, f"one category claimed the whole budget: {calls}"

    def test_an_empty_window_is_not_an_error(self, monkeypatch):
        self._fake_api(0, monkeypatch=monkeypatch)
        result = biorxiv.search(**self.WINDOW)
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

    def test_an_impossible_day_clamps_forward_to_the_last_real_day(self):
        """31 February is the 28th, not the 1st.

        Falling back to the 1st moves the record most of a month *backwards*,
        which can push it out of the window that was queried — a wrong date
        dressed up as a safe default.
        """
        article = self._article(
            "<MedlineCitation><Article>"
            "<ArticleDate><Year>2026</Year><Month>2</Month><Day>31</Day></ArticleDate>"
            "</Article></MedlineCitation>"
        )
        assert pubmed._best_date(article) == date(2026, 2, 28)

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


def _recent_window(days: int = 8) -> str:
    """A `start/end` path segment ending yesterday.

    Relative, not hardcoded: a pinned window goes stale and the test starts
    asserting things about a period nobody is publishing into any more.
    """
    end = date.today() - timedelta(days=1)
    return f"{end - timedelta(days=days):%Y-%m-%d}/{end:%Y-%m-%d}"


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

    def test_biorxiv_honours_a_cursor_that_is_not_page_aligned(self):
        """The assumption `_fetch_category` actually rides on.

        It seeks to ``total - limit``, which is almost never a multiple of the
        page size. If the API were to floor that to a page boundary — or reject
        it — the tail-seek would quietly return the wrong records. Asserting
        "page size < 100" instead, as this test used to, defends a constant the
        code no longer contains.
        """
        from sources._http import fetch_json

        window = f"https://api.biorxiv.org/details/biorxiv/{_recent_window()}"
        page_zero = fetch_json(f"{window}/0")["collection"]
        assert len(page_zero) > 7, "window too small to test a mid-page offset"

        offset = fetch_json(f"{window}/7")["collection"]
        assert offset[0]["doi"] == page_zero[7]["doi"], (
            "a cursor of 7 no longer means 'skip 7 records'; the tail-seek in "
            "_fetch_category would land on the wrong page"
        )

    def test_biorxiv_records_still_arrive_oldest_first(self):
        from sources._http import fetch_json

        window = f"https://api.biorxiv.org/details/biorxiv/{_recent_window()}"
        probe = fetch_json(f"{window}/0")
        page_size = len(probe["collection"])
        total = int(probe["messages"][0]["total"])
        assert total > page_size, "window too small to exercise pagination"

        first = sorted(i["date"] for i in probe["collection"])
        last = sorted(
            i["date"] for i in fetch_json(f"{window}/{total - page_size}")["collection"]
        )
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
        # Without this the test can pass vacuously: a quiet week fits in one
        # page, the tail-seek never runs, and nothing is actually exercised.
        assert result.truncated, "window did not exceed one page; nothing was seeked"
        newest = max(p.published_date for p in result.papers if p.published_date)
        assert (date.today() - newest).days <= 3, (
            f"newest record is {newest}; the fetch is returning stale pages"
        )

    def test_arxiv_structured_queries_still_work(self):
        since = date.today() - timedelta(days=14)
        result = arxiv.search(categories=["q-bio.BM", "q-bio.QM"], since=since, max_results=5)
        assert result.papers, "q-bio should have submissions this fortnight"
        assert all(
            any(c.startswith("q-bio") for c in p.categories) for p in result.papers
        )
        # The cat: clause is checked above; this checks submittedDate:. Without
        # it arXiv could ignore the date range entirely and the test would pass.
        oldest = min(p.published_date for p in result.papers if p.published_date)
        assert oldest >= since, f"submittedDate was ignored: got {oldest} for since={since}"
        assert result.available and result.available >= len(result.papers)

    def test_arxiv_rejects_operators_wrapped_in_a_field_prefix(self):
        """The invariant, stated without assuming *how* arXiv refuses.

        As of 2026-07-30 this query returns HTTP 400. It used to return HTTP 200
        carrying a one-entry feed titled `Error`, which is what
        `_check_for_error_entry` guards and what the offline fixture pins — a
        fixture proves the string is pinned, not that arXiv still emits it.

        Either refusal is fine. What must never happen is a 200 with plausible
        results, because that means `all:`-wrapping silently stopped filtering
        and every window is quietly unbounded.
        """
        from sources._http import FetchError, fetch_xml

        query = "all:cat:q-bio.BM AND submittedDate:[202607200000 TO 202607300000]"
        try:
            feed = fetch_xml(
                "https://export.arxiv.org/api/query",
                {"search_query": query, "max_results": 5},
            )
        except FetchError:
            return  # refused by status code — the current behaviour

        with pytest.raises(arxiv.ArxivQueryError):
            arxiv._check_for_error_entry(feed)

    def test_arxiv_answers_an_unknown_field_with_zero_results_not_an_error(self):
        """A silent failure with no guard, recorded so the shape is known.

        An unrecognised field prefix returns HTTP 200 and `totalResults` 0 —
        indistinguishable from a genuinely empty window. `build_query` only
        emits `cat:`, `all:` and `submittedDate:`, so nothing here can produce
        it today; a future field added without checking would land in it.
        """
        from sources._http import fetch_xml

        feed = fetch_xml(
            "https://export.arxiv.org/api/query",
            {"search_query": "nosuchfield:xyz", "max_results": 5},
        )
        arxiv._check_for_error_entry(feed)  # no error document to find
        assert arxiv._total_results(feed) == 0, (
            "arXiv now reports unknown fields somehow — the quirks note can be updated"
        )

    def test_europepmc_still_echoes_the_query_it_ran(self):
        """`_check_query_echo` compares against `request.queryString`.

        A rename there is a schema change, and the guard now fails closed — so
        this test tells us the guard is still doing work rather than tripping.
        """
        result = europepmc.search(
            keywords=["cryo-EM"],
            since=date.today() - timedelta(days=30),
            until=date.today() - timedelta(days=1),
            max_results=5,
        )
        assert result.available, (
            "a 30-day cryo-EM preprint window returned nothing — either the "
            "field names in build_query moved, or coverage collapsed"
        )

    def test_sampled_categories_stay_within_the_whitelist(self):
        from sources._http import fetch_json

        for server in biorxiv.SERVERS:
            seen = {
                item["category"]
                for item in fetch_json(
                    f"https://api.biorxiv.org/details/{server}/{_recent_window(60)}/0"
                )["collection"]
            }
            unknown = [c for c in seen if biorxiv._normalise(c) not in
                       {biorxiv._normalise(k) for k in biorxiv.CATEGORIES[server]}]
            assert not unknown, f"add to CATEGORIES[{server!r}]: {unknown}"


class TestTruncationWiringOnEverySource:
    """`available` was tested on one of four source paths.

    Removing the wiring on any of the other three left `truncated: false` on a
    fetch that returned a fraction of the window — the exact silence that
    `SearchResult` exists to break.
    """

    def test_arxiv_reports_the_feeds_total(self, monkeypatch):
        feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            "<opensearch:totalResults>79</opensearch:totalResults>"
            '<entry><id>http://arxiv.org/abs/2601.01234v1</id>'
            "<title>A study of protein folding kinetics</title>"
            "<summary>x</summary><published>2026-07-29T00:00:00Z</published>"
            "<updated>2026-07-29T00:00:00Z</updated>"
            '<author><name>Wei Zhang</name></author>'
            '<category term="q-bio.BM"/></entry></feed>'
        )
        monkeypatch.setattr(arxiv, "fetch_xml", lambda *a, **k: ET.fromstring(feed))
        result = arxiv.search(categories=["q-bio.BM"], since=date(2026, 7, 24), max_results=1)
        assert result.available == 79
        assert result.truncated is True

    def test_europepmc_reports_its_hit_count(self, monkeypatch):
        query_seen = {}

        def fetch(url, params=None, **kw):
            query_seen.update(params or {})
            return {
                "request": {"queryString": params["query"]},
                "hitCount": 2733,
                "resultList": {"result": [{"id": "PPR1", "doi": "10.64898/a",
                                           "title": "A preprint about transporters",
                                           "firstPublicationDate": "2026-07-29"}]},
                "nextCursorMark": "*",
            }

        monkeypatch.setattr(europepmc, "fetch_json", fetch)
        result = europepmc.search(
            keywords=["cryo-EM"], since=date(2026, 7, 24), until=date(2026, 7, 30), max_results=1
        )
        assert result.available == 2733
        assert result.truncated is True

    def test_a_missing_query_echo_fails_closed(self, monkeypatch):
        """A renamed field is what a schema change looks like.

        Treating it as "nothing to compare, carry on" disables the guard
        exactly when it is needed — and every record still arrives stamped
        keyword_match, so unrelated results are flagged as the ones to read
        first.
        """
        monkeypatch.setattr(
            europepmc,
            "fetch_json",
            lambda *a, **k: {"hitCount": 2733, "resultList": {"result": []}},
        )
        with pytest.raises(europepmc.EuropePmcQueryError, match="did not echo"):
            europepmc.search(keywords=["cryo-EM"], since=date(2026, 7, 24))


class TestPubmedRequestAndReconciliation:
    """pubmed.search had no end-to-end offline test at all."""

    def _stub(self, monkeypatch, *, count, ids, xml):
        seen = {}

        def fetch_json(url, params=None, **kw):
            seen.update(params or {})
            return {"esearchresult": {"count": str(count), "idlist": list(ids)}}

        monkeypatch.setattr(pubmed, "fetch_json", fetch_json)
        monkeypatch.setattr(pubmed, "fetch_xml", lambda *a, **k: ET.fromstring(xml))
        return seen

    def _article(self, pmid, entrez="2026/07/29"):
        year, month, day = entrez.split("/")
        return (
            f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>"
            f"<Article><ArticleTitle>Paper {pmid}</ArticleTitle>"
            f"<ArticleDate><Year>2026</Year><Month>3</Month><Day>2</Day></ArticleDate>"
            f"</Article></MedlineCitation><PubmedData><History>"
            f'<PubMedPubDate PubStatus="entrez"><Year>{year}</Year>'
            f"<Month>{month}</Month><Day>{day}</Day></PubMedPubDate>"
            f"</History></PubmedData></PubmedArticle>"
        )

    def test_the_window_is_sent_on_the_entrez_axis_in_ncbis_own_format(self, monkeypatch):
        """Slashes, not ISO dashes, and `edat` rather than `pdat`."""
        seen = self._stub(
            monkeypatch, count=1, ids=["1"],
            xml=f"<PubmedArticleSet>{self._article('1')}</PubmedArticleSet>",
        )
        pubmed.search(keywords=["cryo-EM"], since=date(2026, 7, 24), until=date(2026, 7, 30))
        assert seen["datetype"] == "edat"
        assert seen["mindate"] == "2026/07/24"
        assert seen["maxdate"] == "2026/07/30"

    def test_covers_is_measured_on_the_axis_the_search_used(self, monkeypatch):
        """Publication dates answer a different question than the window asked.

        A seven-day Entrez window routinely holds papers published months
        earlier, so a published-date span reads as if the sweep reached far
        outside its own window.
        """
        self._stub(
            monkeypatch, count=1, ids=["1"],
            xml=f"<PubmedArticleSet>{self._article('1', entrez='2026/07/29')}</PubmedArticleSet>",
        )
        result = pubmed.search(keywords=["x"], since=date(2026, 7, 24), until=date(2026, 7, 30))
        assert result.date_axis == "entrez_date"
        assert result.covered_range == (date(2026, 7, 29), date(2026, 7, 29))
        assert result.papers[0].published_date == date(2026, 3, 2), "the article date is kept too"

    def test_unparseable_records_are_named_not_laundered_into_truncation(self, monkeypatch):
        """Telling the caller to raise --max-per-source would change nothing."""
        articles = self._article("1") + "<PubmedArticle><MedlineCitation/></PubmedArticle>"
        self._stub(
            monkeypatch, count=2, ids=["1", "2"],
            xml=f"<PubmedArticleSet>{articles}</PubmedArticleSet>",
        )
        result = pubmed.search(keywords=["x"], since=date(2026, 7, 24), until=date(2026, 7, 30))
        assert len(result) == 1
        assert result.truncated is False, "a dropped record is not a short window"
        assert result.notes and "not parsed" in result.notes[0]

    def test_a_genuinely_short_fetch_still_reports_truncation(self, monkeypatch):
        self._stub(
            monkeypatch, count=556, ids=["1"],
            xml=f"<PubmedArticleSet>{self._article('1')}</PubmedArticleSet>",
        )
        result = pubmed.search(
            keywords=["x"], since=date(2026, 7, 24), until=date(2026, 7, 30), max_results=1
        )
        assert result.available == 556
        assert result.truncated is True


class TestEuropePmcMarkup:
    """Titles arrive with inline HTML that silently breaks the title rule."""

    def test_markup_is_stripped_from_titles_and_abstracts(self):
        record = {
            "id": "PPR1",
            "doi": "10.64898/x",
            "title": "Auxin transport by peptidyl-prolyl <i>cis-trans</i> isomerization",
            "abstractText": "We show <sup>13</sup>C labelling works.",
            "firstPublicationDate": "2026-07-29",
        }
        paper = europepmc._to_paper(record)
        assert paper.title == "Auxin transport by peptidyl-prolyl cis-trans isomerization"
        assert paper.abstract == "We show 13C labelling works."

    def test_a_marked_up_title_still_fingerprints_like_its_biorxiv_twin(self):
        """The reason this matters, stated as the assertion.

        title_fingerprint keeps letters and digits, so an <i> survives as a
        literal "i" inside the fingerprint and the two records stop matching.
        Their DOIs happen to agree here; a preprint and its journal version
        would not, and the merge would simply be lost.
        """
        import dedup

        plain = "Auxin transport by peptidyl-prolyl cis-trans isomerization of ABCB1"
        marked = "Auxin transport by peptidyl-prolyl <i>cis-trans</i> isomerization of ABCB1"
        paper = europepmc._to_paper({"id": "PPR1", "title": marked})
        assert dedup.title_fingerprint(paper.title) == dedup.title_fingerprint(plain)

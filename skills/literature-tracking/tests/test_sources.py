"""Source adapters: query construction, silent-failure guards, parsing.

Everything here is offline except the tests marked ``live``, which check that
the API's real behaviour still matches what ``references/source-quirks.md``
documents. Run those with ``uv run pytest -m live``.
"""

from datetime import date
from xml.etree import ElementTree as ET

import pytest
from sources import arxiv, biorxiv, pubmed

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

    def test_arxiv_structured_queries_still_work(self):
        found = arxiv.search(
            categories=["q-bio.BM", "q-bio.QM"],
            since=date.today().replace(day=1),
            max_results=3,
        )
        assert found, "q-bio should have submissions this month"
        assert all(any(c.startswith("q-bio") for c in p.categories) for p in found)

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

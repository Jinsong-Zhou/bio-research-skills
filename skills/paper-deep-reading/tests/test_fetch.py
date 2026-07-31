"""Reference resolution, and the guards that keep a non-PDF off disk.

Offline except the tests marked ``live``, which re-check that the behaviour
documented in ``references/fulltext-sources.md`` is still what the APIs do.
Run those with ``uv run pytest -m live``.
"""

from pathlib import Path

import _http
import fetch
import pytest
from _http import NotAPdfError, describe_non_pdf, download_pdf

MINIMAL_PDF = b"%PDF-1.7\n" + b"x" * 8192


class TestIdentifierParsing:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("2501.01234", "2501.01234"),
            ("arXiv:2501.01234", "2501.01234"),
            ("arxiv:2501.01234v2", "2501.01234"),
            ("2501.01234v7", "2501.01234"),
            ("https://arxiv.org/abs/2501.01234", "2501.01234"),
            ("https://arxiv.org/pdf/2501.01234v3", "2501.01234"),
            ("q-bio/0701001", "q-bio/0701001"),
            ("math.AG/0701001", "math.AG/0701001"),
            ("cond-mat.stat-mech/0701001", "cond-mat.stat-mech/0701001"),
            ("https://doi.org/10.48550/arXiv.2501.01234", "2501.01234"),
        ],
    )
    def test_arxiv_forms_collapse_to_a_bare_id(self, given, expected):
        """Versions are stripped: v1 and v3 are the same paper, and the bare
        id always resolves to the latest."""
        assert fetch.parse_identifier(given) == ("arxiv", expected)

    @pytest.mark.parametrize(
        "given",
        [
            "10.3389/fnins.2013.00025",
            "10.1371/journal.pone.0301234",
            "10.7554/eLife.12345",
        ],
    )
    def test_a_journal_doi_whose_tail_looks_like_an_arxiv_id_stays_a_doi(self, given):
        """The bug this guards, and it was the worst one here.

        ``10.3389/fnins.2013.00025`` is an ordinary Frontiers DOI whose tail
        has exactly the arXiv shape. Matched with ``search`` rather than
        ``fullmatch``, and tried before the DOI patterns, it became the arXiv
        id ``2013.00025`` — so an open-access paper was reported as having no
        full text, under a ``10.48550/arXiv.…`` DOI this script had invented
        for it and offered to the agent to copy into the note.
        """
        assert fetch.parse_identifier(given) == ("doi", given)

    def test_the_scripts_own_europe_pmc_landing_url_round_trips(self):
        """``resolve_europepmc`` emits this shape, so feeding the script its
        own output has to work. It used to parse as the arXiv id ``MED/…``."""
        kind, value = fetch.parse_identifier("https://europepmc.org/article/MED/1234567")
        assert (kind, value) == ("pmid", "1234567")

    @pytest.mark.parametrize(
        "given",
        [
            "https://pubmed.ncbi.nlm.nih.gov/34265844/",
            "https://pubmed.ncbi.nlm.nih.gov/34265844",
        ],
    )
    def test_a_pubmed_url_works_with_or_without_the_trailing_slash(self, given):
        """The slash is what PubMed's own address bar and its Cite dialog
        emit, and the PMC equivalent already accepted it."""
        assert fetch.parse_identifier(given) == ("pmid", "34265844")

    def test_a_query_string_does_not_become_part_of_the_doi(self):
        """A preprint link copied out of a feed reader carries ``?rss=1``."""
        kind, value = fetch.parse_identifier(
            "https://www.biorxiv.org/content/10.1101/2024.01.15.575681v1?rss=1"
        )
        assert (kind, value) == ("doi", "10.1101/2024.01.15.575681")

    @pytest.mark.parametrize(
        "given",
        ["1234567890", "Smith et al. 2024 Nature 12345678", "cond-mat/070100"],
    )
    def test_a_near_miss_is_refused_rather_than_silently_trimmed(self, given):
        """``search`` discarded whatever did not fit and looked up the rest, so
        a ten-digit number became a nine-digit PMID for a real, unrelated
        paper. A reference we cannot read has to fail, not resolve to a guess.
        """
        with pytest.raises(fetch.ResolutionError):
            fetch.parse_identifier(given)

    def test_a_remote_pdf_url_is_not_mistaken_for_a_local_file(self):
        """The bug this guards: a landing URL ending in .pdf has a .pdf suffix,
        so a suffix check alone reads every preprint link as a path on disk."""
        kind, value = fetch.parse_identifier(
            "https://www.biorxiv.org/content/10.64898/2026.07.16.739021v2.full.pdf"
        )
        assert (kind, value) == ("doi", "10.64898/2026.07.16.739021")

    def test_a_landing_url_yields_the_doi_without_its_version_suffix(self):
        kind, value = fetch.parse_identifier(
            "https://www.biorxiv.org/content/10.1101/2024.01.15.575681v1"
        )
        assert (kind, value) == ("doi", "10.1101/2024.01.15.575681")

    @pytest.mark.parametrize(
        "given",
        ["10.1038/s41586-021-03819-2", "https://doi.org/10.1038/s41586-021-03819-2"],
    )
    def test_doi_resolver_prefixes_are_stripped(self, given):
        assert fetch.parse_identifier(given) == ("doi", "10.1038/s41586-021-03819-2")

    @pytest.mark.parametrize(
        "given", ["PMC8371605", "pmc8371605", "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8371605/"]
    )
    def test_pmcid_forms(self, given):
        assert fetch.parse_identifier(given) == ("pmcid", "PMC8371605")

    @pytest.mark.parametrize("given", ["PMID:34265844", "34265844"])
    def test_bare_digits_are_a_pmid(self, given):
        """Unambiguous: every arXiv id contains a dot or a slash."""
        assert fetch.parse_identifier(given) == ("pmid", "34265844")

    def test_a_local_path_is_returned_absolute(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(MINIMAL_PDF)
        assert fetch.parse_identifier(str(pdf)) == ("local", str(pdf))

    def test_an_unrecognisable_reference_says_what_is_accepted(self):
        with pytest.raises(fetch.ResolutionError, match="arXiv id"):
            fetch.parse_identifier("read me that paper about proteins")


class TestPublishedField:
    @pytest.mark.parametrize("given", ["NA", "na", "", "   ", None])
    def test_unpublished_markers_do_not_become_a_doi(self, given):
        """bioRxiv writes the literal string "NA", not an empty field, so a
        truthiness check reports every preprint as already published."""
        assert fetch._published_doi(given) is None

    def test_a_real_journal_doi_is_carried_through(self):
        assert fetch._published_doi(" 10.1038/s41586-021-03819-2 ") == (
            "10.1038/s41586-021-03819-2"
        )


class TestDownloadGuards:
    def _serve(self, monkeypatch, body: bytes):
        monkeypatch.setattr(_http, "fetch", lambda *a, **k: body)

    def test_a_real_pdf_is_written(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, MINIMAL_PDF)
        dest = tmp_path / "out.pdf"
        assert download_pdf("https://example.invalid/x.pdf", dest) == len(MINIMAL_PDF)
        assert dest.read_bytes() == MINIMAL_PDF

    def test_an_html_page_served_with_200_is_refused(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, b"<!DOCTYPE html><html><body>Sign in</body></html>" + b" " * 9000)
        dest = tmp_path / "out.pdf"
        with pytest.raises(NotAPdfError):
            download_pdf("https://example.invalid/x.pdf", dest)
        assert not dest.exists(), "a refused download must leave nothing behind"

    def test_a_pdf_too_small_to_be_a_paper_is_refused(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, b"%PDF-1.4\nplaceholder")
        with pytest.raises(NotAPdfError):
            download_pdf("https://example.invalid/x.pdf", tmp_path / "out.pdf")

    def test_the_refusal_explains_what_arrived_instead(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, b"<html>cloudflare captcha</html>" + b" " * 9000)
        with pytest.raises(NotAPdfError) as excinfo:
            download_pdf("https://example.invalid/x.pdf", tmp_path / "out.pdf")
        assert "browser" in describe_non_pdf(excinfo.value)

    def test_a_paywall_page_is_described_as_one(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, b"<html>Purchase access</html>" + b" " * 9000)
        with pytest.raises(NotAPdfError) as excinfo:
            download_pdf("https://example.invalid/x.pdf", tmp_path / "out.pdf")
        assert "paywall" in describe_non_pdf(excinfo.value)


class TestRouting:
    def test_both_preprint_prefixes_go_to_the_preprint_servers(self, monkeypatch):
        """Neither prefix maps to one server and neither can be dropped:
        medRxiv issues both, and so does bioRxiv."""
        seen = []
        monkeypatch.setattr(fetch, "resolve_preprint", lambda doi: seen.append(doi) or {})
        for doi in ("10.1101/2024.01.15.575681", "10.64898/2026.07.16.739021"):
            fetch.resolve("doi", doi)
        assert seen == ["10.1101/2024.01.15.575681", "10.64898/2026.07.16.739021"]

    def test_a_preprint_prefix_the_servers_disown_falls_back_to_europe_pmc(self, monkeypatch):
        def disown(doi):
            raise fetch.ResolutionError("not here")

        monkeypatch.setattr(fetch, "resolve_preprint", disown)
        monkeypatch.setattr(fetch, "resolve_europepmc", lambda k, v: {"kind": "europepmc"})
        assert fetch.resolve("doi", "10.1101/journal.article")["kind"] == "europepmc"

    def test_a_doi_europe_pmc_has_never_heard_of_is_tried_as_a_preprint(self, monkeypatch):
        def unknown(kind, value):
            raise fetch.ResolutionError("no record")

        monkeypatch.setattr(fetch, "resolve_europepmc", unknown)
        monkeypatch.setattr(fetch, "resolve_preprint", lambda doi: {"kind": "biorxiv"})
        assert fetch.resolve("doi", "10.99999/brand.new")["kind"] == "biorxiv"

    def test_a_pmcid_europe_pmc_lacks_is_not_retried_as_a_preprint(self, monkeypatch):
        """Only DOIs get the second chance — a PMCID is meaningless to bioRxiv."""
        def unknown(kind, value):
            raise fetch.ResolutionError("no record")

        monkeypatch.setattr(fetch, "resolve_europepmc", unknown)
        with pytest.raises(fetch.ResolutionError):
            fetch.resolve("pmcid", "PMC999999999")

    def test_a_doi_nobody_has_says_both_routes_were_tried(self, monkeypatch):
        """The preprint error alone reads as "this is not a preprint", which
        misdescribes a DOI that simply does not exist anywhere."""
        def unknown(*args):
            raise fetch.ResolutionError("no record")

        monkeypatch.setattr(fetch, "resolve_europepmc", unknown)
        monkeypatch.setattr(fetch, "resolve_preprint", unknown)
        with pytest.raises(fetch.ResolutionError, match="neither Europe PMC nor bioRxiv"):
            fetch.resolve("doi", "10.99999/nothing.here")


class TestReport:
    def test_no_open_access_pdf_reports_abstract_only_with_the_abstract(self, monkeypatch):
        monkeypatch.setattr(
            fetch,
            "resolve",
            lambda kind, value: {
                "kind": "europepmc",
                "id": "PMC1",
                "pdf_url": None,
                "abstract": "We report a structure.",
                "open_access": False,
                "metadata": {"title": "A paper"},
            },
        )
        report = fetch.build_report("10.1073/pnas.1234567890", Path("/nonexistent"))
        assert report["fulltext"] == "abstract-only"
        assert report["path"] is None
        assert report["abstract"] == "We report a structure."
        assert any("not open access" in w for w in report["warnings"])
        assert any("no open-access PDF" in w for w in report["warnings"])

    def test_a_download_that_returns_a_login_page_degrades_rather_than_saving_it(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            fetch,
            "resolve",
            lambda kind, value: {"kind": "biorxiv", "id": "10.64898/x", "pdf_url": "https://x/y"},
        )
        monkeypatch.setattr(_http, "fetch", lambda *a, **k: b"<html>Sign in</html>" + b" " * 9000)
        report = fetch.build_report("10.64898/x", tmp_path)
        assert report["fulltext"] == "abstract-only"
        assert report["path"] is None
        assert not list(tmp_path.iterdir())

    def test_a_preprint_with_a_journal_version_says_so(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            fetch,
            "resolve",
            lambda kind, value: {
                "kind": "biorxiv",
                "id": "10.64898/x",
                "pdf_url": "https://x/y",
                "published_as": "10.1038/s41586-021-03819-2",
            },
        )
        monkeypatch.setattr(_http, "fetch", lambda *a, **k: MINIMAL_PDF)
        report = fetch.build_report("10.64898/x", tmp_path)
        assert report["fulltext"] == "full"
        assert any("later published as" in w for w in report["warnings"])

    def test_a_local_file_that_is_not_a_pdf_is_flagged_not_rejected(self, tmp_path):
        """The user may have a valid reason; say so and let them decide."""
        odd = tmp_path / "paper.pdf"
        odd.write_bytes(b"this is not a pdf at all")
        report = fetch.build_report(str(odd), tmp_path)
        assert report["fulltext"] == "full"
        assert any("%PDF-" in w for w in report["warnings"])

    def test_the_filename_is_safe_for_a_doi(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            fetch,
            "resolve",
            lambda kind, value: {
                "kind": "biorxiv",
                "id": "10.64898/2026.07.16.739021",
                "pdf_url": "https://x/y",
            },
        )
        monkeypatch.setattr(_http, "fetch", lambda *a, **k: MINIMAL_PDF)
        report = fetch.build_report("10.64898/2026.07.16.739021", tmp_path)
        assert Path(report["path"]).name == "10.64898-2026.07.16.739021.pdf"


@pytest.mark.live
class TestLiveBehaviour:
    def test_biorxiv_still_accepts_the_new_doi_prefix(self):
        """If this fails, 10.64898 was retired and the prefix list needs a look."""
        payload = _http.fetch_json("https://api.biorxiv.org/details/biorxiv/2026-07-20/2026-07-22/0")
        prefixes = {e["doi"].split("/")[0] + "/" for e in payload.get("collection", [])}
        assert prefixes, "bioRxiv returned no records for a known-good window"
        assert prefixes <= set(fetch.PREPRINT_DOI_PREFIXES), (
            f"bioRxiv is issuing a prefix we do not route: {prefixes}"
        )

    def test_arxiv_serves_a_pdf_at_the_constructed_url(self, tmp_path):
        resolved = fetch.resolve_arxiv("1706.03762")
        assert download_pdf(resolved["pdf_url"], tmp_path / "a.pdf") > 100_000

    def test_europe_pmc_reports_open_access_and_a_pdf_url_for_an_oa_record(self):
        resolved = fetch.resolve_europepmc("pmcid", "PMC13222519")
        assert resolved["open_access"] is True
        assert resolved["pdf_url"]

    def test_a_closed_access_record_yields_an_abstract_and_no_pdf(self):
        resolved = fetch.resolve_europepmc("doi", "10.1073/pnas.2513585123")
        assert resolved["open_access"] is False
        assert resolved["pdf_url"] is None
        assert resolved["abstract"]

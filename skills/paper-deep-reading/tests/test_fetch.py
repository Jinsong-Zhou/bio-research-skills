"""Reference resolution, and the guards that keep a non-PDF off disk.

Offline except the tests marked ``live``, which re-check that the behaviour
documented in ``references/fulltext-sources.md`` is still what the APIs do.
Run those with ``uv run pytest -m live``.
"""

from pathlib import Path

import _http
import fetch
import pytest
from _http import NotAPdfError, Response, TruncatedPdfError, describe_non_pdf, download_pdf

#: A body that clears every guard: the magic bytes, the size floor, and the
#: trailer that says it is not cut short.
MINIMAL_PDF = b"%PDF-1.7\n" + b"x" * 8192 + b"\n%%EOF\n"


def served(body: bytes, url: str = "https://example.invalid/x.pdf", length=...) -> Response:
    """A response carrying ``body``, with a Content-Length that agrees with it.

    ``length`` is explicit in the tests that care: a header disagreeing with
    the body is exactly what truncation looks like.
    """
    return Response(body, url, len(body) if length is ... else length)


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
    def _serve(self, monkeypatch, body: bytes, **kwargs):
        monkeypatch.setattr(_http, "fetch_response", lambda *a, **k: served(body, **kwargs))

    def test_a_real_pdf_is_written(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, MINIMAL_PDF)
        dest = tmp_path / "out.pdf"
        assert download_pdf("https://example.invalid/x.pdf", dest).body == MINIMAL_PDF
        assert dest.read_bytes() == MINIMAL_PDF

    def test_a_body_shorter_than_its_content_length_is_refused(self, monkeypatch, tmp_path):
        """What truncation removes is the end — the discussion, the
        limitations, the supplementary material, which is most of what an
        assessment is built from. A magic-byte check cannot see it, and the
        file opens fine."""
        self._serve(monkeypatch, MINIMAL_PDF, length=len(MINIMAL_PDF) + 5000)
        dest = tmp_path / "out.pdf"
        with pytest.raises(TruncatedPdfError, match="of .* bytes"):
            download_pdf("https://example.invalid/x.pdf", dest)
        assert not dest.exists()

    def test_a_pdf_with_no_end_marker_is_refused(self, monkeypatch, tmp_path):
        """The other truncation shape: no Content-Length to compare against,
        so the missing %%EOF is the only evidence."""
        self._serve(monkeypatch, b"%PDF-1.7\n" + b"x" * 8192, length=None)
        dest = tmp_path / "out.pdf"
        with pytest.raises(TruncatedPdfError, match="no end marker"):
            download_pdf("https://example.invalid/x.pdf", dest)
        assert not dest.exists()

    def test_the_address_the_bytes_came_from_is_returned(self, monkeypatch, tmp_path):
        """A caller that records only the requested URL cannot show where a
        redirect ended up."""
        self._serve(monkeypatch, MINIMAL_PDF, url="https://cdn.example.invalid/real.pdf")
        response = download_pdf("https://example.invalid/x.pdf", tmp_path / "out.pdf")
        assert response.url == "https://cdn.example.invalid/real.pdf"

    def test_a_bad_pdf_is_caught_by_except_fetch_error(self, monkeypatch, tmp_path):
        """download_pdf documents NotAPdfError and FetchError together as its
        raise set; a guard that did not actually catch the first would have a
        hole in it exactly where a paywall page gets saved as paper.pdf."""
        self._serve(monkeypatch, b"<html>Sign in</html>" + b" " * 9000)
        with pytest.raises(_http.FetchError):
            download_pdf("https://example.invalid/x.pdf", tmp_path / "out.pdf")

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

    @pytest.mark.parametrize(
        ("head", "size", "expected"),
        [
            (b"<html>cloudflare captcha</html>", 9000, "browser"),
            (b"<!DOCTYPE html><body>Purchase access", 9000, "paywall"),
            (b"%PDF-1.4\nplaceholder", 20, "too small to be a paper"),
            (b"\x00\x01\x02 binary junk", 9000, "not a PDF"),
        ],
    )
    def test_each_kind_of_wrong_body_gets_its_own_advice(self, head, size, expected):
        """A bot check, a paywall, a placeholder and a mystery need four
        different next moves. The paywall case used to be asserted against a
        branch that returns the same string for *any* HTML, so it would have
        passed with the discrimination deleted."""
        assert expected in describe_non_pdf(NotAPdfError("https://x/y", head, size))


def biorxiv_payload(doi="10.64898/2026.07.16.739021", **overrides):
    """A two-version bioRxiv response, shaped like the real one.

    The details that matter are the ones measured in
    ``references/fulltext-sources.md``: one entry per version oldest first,
    ``published`` as the literal string ``"NA"`` rather than an empty field,
    and authors separated by semicolons.
    """
    def version(n, title):
        return {
            "doi": doi,
            "title": title,
            "version": str(n),
            "authors": "Dutta, S.; Other, A.; Third, B.",
            "date": "2026-07-16",
            "category": "cell biology",
            "abstract": "We report a mechanism.",
            "published": "NA",
            **overrides,
        }

    return {"collection": [version(1, "First submission"), version(2, "Revised title")]}


class TestResolvePreprint:
    """The bioRxiv/medRxiv route — one of the three the skill advertises, and
    until now executed by no test in either suite."""

    DOI = "10.64898/2026.07.16.739021"

    @staticmethod
    def _server_of(url: str) -> str:
        # Counting path segments from the right does not work here: the DOI
        # itself contains a slash.
        return "medrxiv" if "/medrxiv/" in url else "biorxiv"

    def _serve(self, monkeypatch, by_server):
        calls = []

        def fake(url, *args, **kwargs):
            calls.append(self._server_of(url))
            outcome = by_server.get(self._server_of(url))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome if outcome is not None else {"collection": []}

        monkeypatch.setattr(fetch, "fetch_json", fake)
        return calls

    def test_the_latest_version_is_the_one_resolved(self, monkeypatch):
        """The API returns one entry per version, oldest first. Reading the
        first would silently hand back v1 of a paper now at v2 — and the v1
        URL still serves a valid PDF, so nothing downstream would notice."""
        self._serve(monkeypatch, {"biorxiv": biorxiv_payload(self.DOI)})
        resolved = fetch.resolve_preprint(self.DOI)
        assert resolved["version"] == "2"
        assert resolved["metadata"]["title"] == "Revised title"
        assert resolved["pdf_url"].endswith("v2.full.pdf")

    def test_medrxiv_is_tried_when_biorxiv_has_no_record(self, monkeypatch):
        calls = self._serve(monkeypatch, {"medrxiv": biorxiv_payload(self.DOI)})
        assert fetch.resolve_preprint(self.DOI)["kind"] == "medrxiv"
        assert calls == ["biorxiv", "medrxiv"]

    def test_an_unpublished_preprint_is_not_reported_as_published(self, monkeypatch):
        """bioRxiv writes the literal "NA". A truthiness check here reported
        every preprint as already published — the bug _published_doi exists
        for, tested in isolation but never through the code that calls it."""
        self._serve(monkeypatch, {"biorxiv": biorxiv_payload(self.DOI)})
        assert fetch.resolve_preprint(self.DOI)["published_as"] is None

    def test_a_journal_version_is_carried_through(self, monkeypatch):
        self._serve(
            monkeypatch,
            {"biorxiv": biorxiv_payload(self.DOI, published="10.1038/s41586-021-03819-2")},
        )
        assert fetch.resolve_preprint(self.DOI)["published_as"] == (
            "10.1038/s41586-021-03819-2"
        )

    def test_authors_are_split_on_semicolons(self, monkeypatch):
        """Live data is "Dutta, S.; Other, A." — splitting on commas shatters
        each name into surname and initial."""
        self._serve(monkeypatch, {"biorxiv": biorxiv_payload(self.DOI)})
        assert fetch.resolve_preprint(self.DOI)["metadata"]["authors"] == [
            "Dutta, S.", "Other, A.", "Third, B."
        ]

    def test_the_abstract_is_carried_through(self, monkeypatch):
        """It used to be dropped, so a preprint whose PDF failed reported
        "abstract-only" with no abstract — the one state in which there is
        nothing at all to write from."""
        self._serve(monkeypatch, {"biorxiv": biorxiv_payload(self.DOI)})
        assert fetch.resolve_preprint(self.DOI)["abstract"] == "We report a mechanism."

    def test_a_record_for_a_different_doi_is_refused(self, monkeypatch):
        self._serve(monkeypatch, {"biorxiv": biorxiv_payload(doi="10.64898/someone.else")})
        with pytest.raises(fetch.IdentityMismatchError, match="someone.else"):
            fetch.resolve_preprint(self.DOI)

    def test_a_server_that_did_not_answer_is_not_quoted_as_saying_no(self, monkeypatch):
        """`except FetchError: continue` made a 503 read exactly like "no such
        record", so an outage became the factual claim "this is not a
        preprint" — about a paper whose PDF was one request away."""
        self._serve(
            monkeypatch,
            {
                "biorxiv": _http.FetchError("HTTP 503"),
                "medrxiv": _http.FetchError("HTTP 503"),
            },
        )
        with pytest.raises(fetch.SourceUnavailableError, match="unknown rather than answered"):
            fetch.resolve_preprint(self.DOI)

    def test_a_genuine_absence_still_says_so(self, monkeypatch):
        """Both servers answered, neither has it. That is a fact about the
        paper, and it must not be dressed up as an outage."""
        self._serve(monkeypatch, {})
        with pytest.raises(fetch.ResolutionError, match="not on bioRxiv or medRxiv") as excinfo:
            fetch.resolve_preprint(self.DOI)
        assert not isinstance(excinfo.value, fetch.SourceUnavailableError)


class TestIdentityChecks:
    def _epmc(self, monkeypatch, record):
        monkeypatch.setattr(
            fetch, "fetch_json", lambda *a, **k: {"resultList": {"result": [record]}}
        )

    def test_europe_pmc_answering_with_a_different_paper_is_refused(self, monkeypatch):
        """The query language is exact, so a disagreement is not a near miss.
        Every field downstream — title, authors, PDF URL — would describe the
        other paper while the report kept the requested reference on top."""
        self._epmc(monkeypatch, {"doi": "10.1038/other", "id": "999", "title": "Someone else"})
        with pytest.raises(fetch.IdentityMismatchError, match="10.1038/other"):
            fetch.resolve_europepmc("doi", "10.1073/pnas.1234567890")

    def test_a_case_difference_is_not_a_mismatch(self, monkeypatch):
        """DOIs are case-insensitive; refusing on case would break lookups."""
        self._epmc(monkeypatch, {"doi": "10.1038/S41586-021-03819-2", "id": "1"})
        assert fetch.resolve_europepmc("doi", "10.1038/s41586-021-03819-2")

    def test_a_record_that_omits_the_field_is_not_a_mismatch(self, monkeypatch):
        """Absence is not disagreement, and refusing on it would break records
        Europe PMC simply does not carry a DOI for."""
        self._epmc(monkeypatch, {"id": "34265844", "title": "A paper"})
        assert fetch.resolve_europepmc("doi", "10.1038/s41586-021-03819-2")

    def test_a_mismatch_is_not_papered_over_by_the_fallback(self, monkeypatch):
        """The other routes exist for "nobody has this", not for "somebody
        answered with the wrong thing"."""
        def wrong(kind, value):
            raise fetch.IdentityMismatchError("answered with a record for 10.1038/other")

        monkeypatch.setattr(fetch, "resolve_europepmc", wrong)
        monkeypatch.setattr(fetch, "resolve_preprint", lambda doi: {"kind": "biorxiv"})
        with pytest.raises(fetch.IdentityMismatchError):
            fetch.resolve("doi", "10.9999/x")


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

    def test_a_fallback_covering_for_an_outage_says_so(self, monkeypatch):
        """bioRxiv 503s, Europe PMC has a metadata-only record for the same
        preprint, and the user is told there is no open-access PDF — for a
        paper whose PDF is one request away. Without this warning the
        substitution is invisible."""
        def unreachable(doi):
            raise fetch.SourceUnavailableError("could not reach biorxiv (HTTP 503)")

        monkeypatch.setattr(fetch, "resolve_preprint", unreachable)
        monkeypatch.setattr(fetch, "resolve_europepmc", lambda k, v: {"kind": "europepmc"})
        resolved = fetch.resolve("doi", "10.1101/2024.01.15.575681")
        assert any("could not reach biorxiv" in w for w in resolved["warnings"])
        assert any("Europe PMC instead" in w for w in resolved["warnings"])

    def test_a_clean_fallback_adds_no_warning(self, monkeypatch):
        """A preprint-prefixed DOI that is genuinely a journal article is an
        ordinary outcome, not something to caveat."""
        def disown(doi):
            raise fetch.ResolutionError("not here")

        monkeypatch.setattr(fetch, "resolve_preprint", disown)
        monkeypatch.setattr(fetch, "resolve_europepmc", lambda k, v: {"kind": "europepmc"})
        assert "warnings" not in fetch.resolve("doi", "10.1101/journal.article")

    def test_two_unreachable_sources_do_not_become_a_verdict(self, monkeypatch):
        """Neither answered, so "nobody has this" is not something we know."""
        def unreachable(*args):
            raise fetch.SourceUnavailableError("could not reach it")

        monkeypatch.setattr(fetch, "resolve_europepmc", unreachable)
        monkeypatch.setattr(fetch, "resolve_preprint", unreachable)
        with pytest.raises(fetch.SourceUnavailableError, match="could not be looked up"):
            fetch.resolve("doi", "10.99999/nothing.here")

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
        monkeypatch.setattr(
            _http,
            "fetch_response",
            lambda *a, **k: served(b"<html>Sign in</html>" + b" " * 9000),
        )
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
        monkeypatch.setattr(_http, "fetch_response", lambda *a, **k: served(MINIMAL_PDF))
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
        monkeypatch.setattr(_http, "fetch_response", lambda *a, **k: served(MINIMAL_PDF))
        report = fetch.build_report("10.64898/2026.07.16.739021", tmp_path)
        assert Path(report["path"]).name == "10.64898-2026.07.16.739021.pdf"


@pytest.mark.live
class TestLiveBehaviour:
    WINDOW = "https://api.biorxiv.org/details/biorxiv/2026-07-20/2026-07-22/0"

    def test_biorxiv_issues_no_prefix_we_do_not_route(self):
        payload = _http.fetch_json(self.WINDOW)
        prefixes = {e["doi"].split("/")[0] + "/" for e in payload.get("collection", [])}
        assert prefixes, "bioRxiv returned no records for a known-good window"
        assert prefixes <= set(fetch.PREPRINT_DOI_PREFIXES), (
            f"bioRxiv is issuing a prefix we do not route: {prefixes}"
        )

    def test_the_newer_prefix_is_still_in_use(self):
        """The subset assertion above cannot catch a *retired* prefix — if
        10.64898 vanished, the remaining set would still be a subset and the
        test would pass. This is the other direction, which is what the
        docstring up there used to claim and could not deliver."""
        payload = _http.fetch_json(self.WINDOW)
        prefixes = {e["doi"].split("/")[0] + "/" for e in payload.get("collection", [])}
        assert "10.64898/" in prefixes, (
            f"10.64898 is no longer being issued; the prefix list needs a look ({prefixes})"
        )

    def test_an_unpublished_preprint_still_carries_the_literal_string_NA(self):
        """_published_doi is built around this. Unit-tested against a constant
        the test itself supplies, which re-checks our own code rather than
        upstream — if bioRxiv switched to "n/a", both suites stayed green and
        every preprint got a false "later published as" warning."""
        payload = _http.fetch_json(self.WINDOW)
        published = {str(e.get("published")) for e in payload.get("collection", [])}
        assert "NA" in published, f"bioRxiv no longer writes NA: {published}"

    def test_the_details_endpoint_answers_a_doi_under_the_newer_prefix(self):
        payload = _http.fetch_json(
            "https://api.biorxiv.org/details/biorxiv/10.64898/2026.07.16.739021"
        )
        assert payload.get("collection"), payload

    def test_a_preprint_resolves_end_to_end_and_serves_a_whole_pdf(self, tmp_path):
        """bioRxiv is the source most likely to answer a PDF request with an
        interstitial, and it was the one whose download was never live-tested."""
        resolved = fetch.resolve_preprint("10.64898/2026.07.16.739021")
        assert resolved["kind"] in ("biorxiv", "medrxiv")
        assert download_pdf(resolved["pdf_url"], tmp_path / "p.pdf").body[:5] == b"%PDF-"

    def test_arxiv_serves_a_pdf_at_the_constructed_url(self, tmp_path):
        resolved = fetch.resolve_arxiv("1706.03762")
        assert len(download_pdf(resolved["pdf_url"], tmp_path / "a.pdf").body) > 100_000

    def test_europe_pmc_reports_open_access_and_a_pdf_url_for_an_oa_record(self):
        resolved = fetch.resolve_europepmc("pmcid", "PMC13222519")
        assert resolved["open_access"] is True
        assert resolved["pdf_url"]

    def test_a_closed_access_record_yields_an_abstract_and_no_pdf(self):
        resolved = fetch.resolve_europepmc("doi", "10.1073/pnas.2513585123")
        assert resolved["open_access"] is False
        assert resolved["pdf_url"] is None
        assert resolved["abstract"]

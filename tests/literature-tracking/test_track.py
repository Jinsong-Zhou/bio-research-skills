"""CLI argument handling and report assembly, fully offline.

"Fully offline" is enforced, not asserted: ``conftest._no_network`` fails any
test here that opens a socket. Two tests in this file used to reach ebi.ac.uk
and pass either way, because ``_collect`` folds the resulting ``FetchError``
into ``report["errors"]`` and nothing checked that list was empty.
"""

import json
from datetime import date, timedelta

import pytest
import track
from models import Paper, SearchResult
from sources._http import FetchError

TODAY = date(2026, 7, 30)
ALL_SOURCES = ("arxiv", "biorxiv", "pubmed", "europepmc")


class TestParseSince:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("7d", date(2026, 7, 24)),
            ("2w", date(2026, 7, 17)),
            ("1m", date(2026, 7, 1)),
            ("1y", date(2025, 7, 31)),
            ("2026-07-01", date(2026, 7, 1)),
            ("  7d  ", date(2026, 7, 24)),
            ("7D", date(2026, 7, 24)),
        ],
    )
    def test_relative_and_absolute_forms(self, given, expected):
        assert track.parse_since(given, today=TODAY) == expected

    @pytest.mark.parametrize("given", ["yesterday", "7 fortnights", "2026-13-01", ""])
    def test_nonsense_is_rejected_rather_than_guessed(self, given):
        with pytest.raises(Exception, match="relative window|ISO date"):
            track.parse_since(given, today=TODAY)


class TestParser:
    def test_defaults(self):
        args = track.build_parser().parse_args([])
        assert args.sources == ["arxiv", "biorxiv", "pubmed", "europepmc"]
        assert args.max_per_source == 200
        assert args.crossref == "auto"

    def test_unknown_source_is_rejected(self, capsys):
        with pytest.raises(SystemExit):
            track.build_parser().parse_args(["--sources", "scholar"])

    def test_the_flag_named_in_the_skipped_lookup_warning_exists(self):
        """The warning told users to raise a dial that was not on the panel."""
        args = track.build_parser().parse_args(["--max-crossref-lookups", "300"])
        assert args.max_crossref_lookups == 300

    def test_keywords_and_categories_are_separate_knobs(self):
        args = track.build_parser().parse_args(
            ["--keywords", "cryo-EM", "folding", "--biorxiv-categories", "biochemistry"]
        )
        assert args.keywords == ["cryo-EM", "folding"]
        assert args.biorxiv_categories == ["biochemistry"]


class TestCrossrefAuto:
    """The rule merges preprint with journal version — both must be in range."""

    def _args(self, crossref, span_days):
        """``span_days`` counts both ends, matching how the CLI reports it."""
        args = track.build_parser().parse_args(["--crossref", crossref])
        args.until = TODAY
        args.since = TODAY - timedelta(days=span_days - 1)
        return args

    def test_a_weekly_window_turns_it_off_with_a_reason(self):
        enabled, reason = track.resolve_crossref(self._args("auto", 7))
        assert enabled is False
        assert "7-day window" in reason and "--crossref on" in reason

    def test_a_wide_window_turns_it_on(self):
        enabled, reason = track.resolve_crossref(self._args("auto", 180))
        assert enabled is True
        assert reason == ""

    @pytest.mark.parametrize("span", [0, 59, 60, 365])
    def test_the_threshold_is_the_only_thing_auto_looks_at(self, span):
        enabled, _ = track.resolve_crossref(self._args("auto", span))
        assert enabled is (span >= track.CROSSREF_MIN_WINDOW_DAYS)

    def test_on_overrides_a_narrow_window(self):
        assert track.resolve_crossref(self._args("on", 7)) == (True, "")

    def test_off_overrides_a_wide_one(self):
        enabled, reason = track.resolve_crossref(self._args("off", 365))
        assert enabled is False
        assert "--crossref off" in reason


class TestReport:
    @pytest.fixture(autouse=True)
    def _stub_every_source(self, monkeypatch):
        """Every source returns nothing unless a test says otherwise.

        Stubbing only the sources a test cares about leaves the rest reaching
        for the real API — which is how two tests in this file ended up making
        live requests while claiming to be offline.
        """
        for name in ALL_SOURCES:
            monkeypatch.setattr(
                getattr(track, name), "search", lambda **kw: SearchResult([], available=0)
            )

    def _args(self, **overrides):
        args = track.build_parser().parse_args([])
        args.since, args.until = date(2026, 7, 23), TODAY
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _paper(self, source, doi, title):
        return Paper(
            paper_id=doi, title=title, authors=["Zhang, Wei"], abstract="",
            doi=doi, published_date=TODAY, url="", pdf_url="", source=source,
        )

    def test_a_failing_source_does_not_take_the_others_down(self, monkeypatch):
        monkeypatch.setattr(
            track.arxiv,
            "search",
            lambda **kw: SearchResult([self._paper("arxiv", "10.1/a", "A study of X")]),
        )
        monkeypatch.setattr(
            track.biorxiv,
            "search",
            lambda **kw: (_ for _ in ()).throw(FetchError("bioRxiv is down")),
        )

        report = track.build_report(self._args(crossref="off"))

        assert len(report["papers"]) == 1
        assert [e["source"] for e in report["errors"]] == ["biorxiv"]
        assert "bioRxiv is down" in report["errors"][0]["error"]
        assert report["stats"]["coverage_by_source"]["biorxiv"]["status"] == "failed"
        assert report["stats"]["coverage_by_source"]["arxiv"]["status"] == "ok"

    def test_an_adapter_bug_is_tolerated_but_labelled_as_one(self, monkeypatch):
        """A renamed upstream field raises KeyError, not FetchError.

        Letting that escape would discard every source already fetched. Folding
        it in silently would report a code defect as an outage — so it is kept,
        and marked.
        """
        monkeypatch.setattr(
            track.arxiv,
            "search",
            lambda **kw: SearchResult([self._paper("arxiv", "10.1/a", "A study of X")]),
        )
        monkeypatch.setattr(
            track.pubmed,
            "search",
            lambda **kw: (_ for _ in ()).throw(KeyError("authorList")),
        )

        report = track.build_report(self._args(crossref="off"))

        assert len(report["papers"]) == 1, "the arXiv results must survive"
        (failure,) = report["errors"]
        assert failure["source"] == "pubmed"
        assert "KeyError" in failure["error"]
        assert "unexpected" in failure["kind"], "a bug must not read as an outage"

    def test_errors_key_is_present_even_on_a_clean_run(self, monkeypatch):
        """Callers need to tell 'nothing new' from 'three sources fell over'."""
        for module in (track.arxiv, track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: SearchResult([]))
        report = track.build_report(self._args(crossref="off"))
        assert report["errors"] == []
        assert report["papers"] == []
        assert report["stats"]["fetched_total"] == 0

    def test_the_query_is_echoed_back_for_reproducibility(self, monkeypatch):
        for module in (track.arxiv, track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: SearchResult([]))
        report = track.build_report(self._args(crossref="off", keywords=["cryo-EM"]))
        assert report["query"]["since"] == "2026-07-23"
        assert report["query"]["keywords"] == ["cryo-EM"]
        assert report["query"]["arxiv_categories"], "the q-bio default should be recorded"

    def test_duplicates_across_sources_are_merged_in_the_report(self, monkeypatch):
        title = "Cryo-EM structure of a bacterial multidrug efflux transporter"
        monkeypatch.setattr(
            track.arxiv,
            "search",
            lambda **kw: SearchResult([self._paper("arxiv", "10.1/a", title)]),
        )
        monkeypatch.setattr(
            track.biorxiv,
            "search",
            lambda **kw: SearchResult([self._paper("biorxiv", "10.2/b", title)]),
        )
        monkeypatch.setattr(track.pubmed, "search", lambda **kw: SearchResult([]))

        report = track.build_report(self._args(crossref="off"))

        assert report["stats"]["fetched_total"] == 2
        assert report["stats"]["unique_total"] == 1
        assert report["stats"]["merges_by_tier"] == {"title-fingerprint": 1}


    def test_truncation_is_reported_with_the_days_actually_covered(self, monkeypatch):
        """A truncated fetch drops the window's early days, not a random slice."""
        recent = [
            self._paper("pubmed", f"10.1/{i}", f"Paper number {i}") for i in range(3)
        ]
        monkeypatch.setattr(
            track.pubmed, "search", lambda **kw: SearchResult(recent, available=556)
        )
        for module in (track.arxiv, track.biorxiv):
            monkeypatch.setattr(module, "search", lambda **kw: SearchResult([], available=0))

        report = track.build_report(self._args(crossref="off"))

        assert report["stats"]["truncated_sources"] == ["pubmed"]
        pubmed_coverage = report["stats"]["coverage_by_source"]["pubmed"]
        assert pubmed_coverage == {
            "status": "ok",
            "reason": None,
            "fetched": 3,
            "available": 556,
            "coverage": "truncated",
            "truncated": True,
            "covers": [TODAY.isoformat(), TODAY.isoformat()],
            "covers_field": "published_date",
            "notes": [],
        }

    def test_a_complete_sweep_is_not_flagged(self, monkeypatch):
        for module in (track.arxiv, track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: SearchResult([], available=0))
        report = track.build_report(self._args(crossref="off"))
        assert report["stats"]["truncated_sources"] == []

    def test_keywords_do_not_reach_arxiv_unless_asked_for(self, monkeypatch):
        """q-bio is small; ANDing keywords onto it cut 79 papers to 1."""
        seen = {}
        monkeypatch.setattr(
            track.arxiv,
            "search",
            lambda **kw: seen.update(kw) or SearchResult([]),
        )
        for module in (track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: SearchResult([]))

        track.build_report(self._args(crossref="off", keywords=["cryo-EM"]))
        assert seen["keywords"] is None, "--keywords must not narrow arXiv"

        track.build_report(
            self._args(crossref="off", keywords=["cryo-EM"], arxiv_keywords=["folding"])
        )
        assert seen["keywords"] == ["folding"], "--arxiv-keywords is the opt-in"


class TestMain:
    def test_an_inverted_window_exits_with_a_usage_error(self, capsys):
        assert track.main(["--since", "2026-07-30", "--until", "2026-07-01"]) == 2
        assert "is after" in capsys.readouterr().err


class TestSourcesNeverVanishSilently:
    """A requested source that does not run must say so, in the JSON.

    `keyword_matched: 0` with no explanation reads as "nothing this week matched
    your interests" — the wrong conclusion, confidently drawn, when the keyword
    channel was never opened at all.
    """

    def _args(self, **overrides):
        args = track.build_parser().parse_args([])
        args.since, args.until = date(2026, 7, 23), TODAY
        args.crossref = "off"
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        for name in ALL_SOURCES:
            monkeypatch.setattr(
                getattr(track, name), "search", lambda **kw: SearchResult([], available=0)
            )

    def test_europepmc_without_keywords_is_recorded_as_skipped(self):
        report = track.build_report(self._args(keywords=None))
        coverage = report["stats"]["coverage_by_source"]["europepmc"]
        assert coverage["status"] == "skipped"
        assert "--keywords" in coverage["reason"]
        assert "europepmc" in report["stats"]["skipped_sources"]

    def test_europepmc_without_a_preprint_server_is_recorded_as_skipped(self):
        report = track.build_report(
            self._args(keywords=["cryo-EM"], sources=["arxiv", "pubmed", "europepmc"])
        )
        coverage = report["stats"]["coverage_by_source"]["europepmc"]
        assert coverage["status"] == "skipped"
        assert "--sources" in coverage["reason"]

    def test_every_requested_source_gets_a_row(self):
        report = track.build_report(self._args(keywords=["cryo-EM"]))
        assert set(report["stats"]["coverage_by_source"]) == set(ALL_SOURCES)

    def test_unknown_coverage_is_listed_separately_from_truncation(self, monkeypatch):
        """"We don't know how much exists" is not "we got it all".

        `truncated` is false in both cases, so without this list an unmeasured
        sweep passes the same check a complete one does.
        """
        monkeypatch.setattr(
            track.arxiv, "search", lambda **kw: SearchResult([], available=None)
        )
        report = track.build_report(self._args())
        assert report["stats"]["unknown_coverage_sources"] == ["arxiv"]
        assert report["stats"]["truncated_sources"] == []

    def test_an_empty_but_complete_sweep_is_not_called_unknown(self):
        """SearchResult defines __len__, so an empty result is falsy.

        A truthiness test on it files every quiet source as unmeasurable.
        """
        report = track.build_report(self._args())
        biorxiv_coverage = report["stats"]["coverage_by_source"]["biorxiv"]
        assert biorxiv_coverage["coverage"] == "complete"
        assert report["stats"]["unknown_coverage_sources"] == []


class TestCrossrefDecisionIsVisible:
    def _args(self, **overrides):
        args = track.build_parser().parse_args([])
        args.since, args.until = date(2026, 7, 23), TODAY
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        for name in ALL_SOURCES:
            monkeypatch.setattr(
                getattr(track, name), "search", lambda **kw: SearchResult([], available=0)
            )

    def test_auto_off_says_so_in_the_json_not_only_on_stderr(self):
        """`lookups: 0` alone cannot distinguish "off" from "ran, found nothing".

        With the default 7-day window auto always turns rule 4 off, so that is
        the state most runs are actually in.
        """
        report = track.build_report(self._args(crossref="auto"))
        crossref = report["stats"]["crossref"]
        assert crossref["enabled"] is False
        assert crossref["requested"] == "auto"
        assert "60 days" in crossref["reason"]
        assert crossref["lookups"] == 0

    def test_a_run_that_did_use_it_reports_no_reason(self):
        report = track.build_report(self._args(crossref="on"))
        assert report["stats"]["crossref"]["enabled"] is True
        assert report["stats"]["crossref"]["reason"] is None


class TestExitContract:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        for name in ALL_SOURCES:
            monkeypatch.setattr(
                getattr(track, name), "search", lambda **kw: SearchResult([], available=0)
            )

    def test_stdout_is_parseable_json(self, capsys):
        """The whole output contract, and nothing asserted it.

        A stray print() to stdout instead of stderr ships green otherwise.
        """
        assert track.main(["--crossref", "off"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"query", "stats", "errors", "papers"}

    def test_a_quiet_week_with_one_flaky_source_is_not_a_failed_run(self, monkeypatch, capsys):
        """Exit 1 there would conflate "nothing new" with "everything fell over"."""
        monkeypatch.setattr(
            track.pubmed,
            "search",
            lambda **kw: (_ for _ in ()).throw(FetchError("PubMed timed out")),
        )
        assert track.main(["--crossref", "off"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["errors"], "the failure must still be reported"

    def test_every_source_failing_is_a_failed_run(self, monkeypatch, capsys):
        for name in ALL_SOURCES:
            monkeypatch.setattr(
                getattr(track, name),
                "search",
                lambda **kw: (_ for _ in ()).throw(FetchError("down")),
            )
        assert track.main(["--crossref", "off", "--keywords", "cryo-EM"]) == 1
        capsys.readouterr()


class TestUsageErrorsStopTheRun:
    """Config mistakes are known before any I/O and must not be tolerated.

    Swallowing them into errors[] produced a plausible digest built on a query
    nobody actually made, and exited 0.
    """

    def test_a_mistyped_subject_area_exits_two_with_a_suggestion(self, capsys):
        assert track.main(["--biorxiv-categories", "biochemstry"]) == 2
        err = capsys.readouterr().err
        assert "not a biorxiv subject area" in err
        assert "biochemistry" in err, "close matches make the fix obvious"

    def test_categories_for_an_unselected_source_are_a_usage_error(self, capsys):
        """Otherwise the report echoes them back as though medRxiv was searched."""
        assert track.main(["--medrxiv-categories", "oncology"]) == 2
        assert "not in --sources" in capsys.readouterr().err

    def test_the_same_categories_are_fine_once_the_source_is_selected(self):
        args = track.build_parser().parse_args(
            ["--sources", "medrxiv", "--medrxiv-categories", "oncology"]
        )
        args.until = TODAY
        assert track.check_usage(args) is None

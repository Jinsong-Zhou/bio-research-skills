"""CLI argument handling and report assembly, fully offline."""

from datetime import date, timedelta

import pytest
import track
from models import Paper, SearchResult

TODAY = date(2026, 7, 30)


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
            lambda **kw: (_ for _ in ()).throw(track.FetchError("bioRxiv is down")),
        )
        monkeypatch.setattr(track.pubmed, "search", lambda **kw: SearchResult([]))

        report = track.build_report(self._args(crossref="off"))

        assert len(report["papers"]) == 1
        assert [e["source"] for e in report["errors"]] == ["biorxiv"]
        assert "bioRxiv is down" in report["errors"][0]["error"]

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
            "fetched": 3,
            "available": 556,
            "truncated": True,
            "covers": [TODAY.isoformat(), TODAY.isoformat()],
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

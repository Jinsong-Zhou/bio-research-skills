"""CLI argument handling and report assembly, fully offline."""

from datetime import date

import pytest
import track
from models import Paper

TODAY = date(2026, 7, 30)


class TestParseSince:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("7d", date(2026, 7, 23)),
            ("2w", date(2026, 7, 16)),
            ("1m", date(2026, 6, 30)),
            ("1y", date(2025, 7, 30)),
            ("2026-07-01", date(2026, 7, 1)),
            ("  7d  ", date(2026, 7, 23)),
            ("7D", date(2026, 7, 23)),
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
        assert args.sources == ["arxiv", "biorxiv", "pubmed"]
        assert args.max_per_source == 200
        assert args.no_crossref is False

    def test_unknown_source_is_rejected(self, capsys):
        with pytest.raises(SystemExit):
            track.build_parser().parse_args(["--sources", "scholar"])

    def test_keywords_and_categories_are_separate_knobs(self):
        args = track.build_parser().parse_args(
            ["--keywords", "cryo-EM", "folding", "--biorxiv-categories", "biochemistry"]
        )
        assert args.keywords == ["cryo-EM", "folding"]
        assert args.biorxiv_categories == ["biochemistry"]


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
            track.arxiv, "search", lambda **kw: [self._paper("arxiv", "10.1/a", "A study of X")]
        )
        monkeypatch.setattr(
            track.biorxiv,
            "search",
            lambda **kw: (_ for _ in ()).throw(track.FetchError("bioRxiv is down")),
        )
        monkeypatch.setattr(track.pubmed, "search", lambda **kw: [])

        report = track.build_report(self._args(no_crossref=True))

        assert len(report["papers"]) == 1
        assert [e["source"] for e in report["errors"]] == ["biorxiv"]
        assert "bioRxiv is down" in report["errors"][0]["error"]

    def test_errors_key_is_present_even_on_a_clean_run(self, monkeypatch):
        """Callers need to tell 'nothing new' from 'three sources fell over'."""
        for module in (track.arxiv, track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: [])
        report = track.build_report(self._args(no_crossref=True))
        assert report["errors"] == []
        assert report["papers"] == []
        assert report["stats"]["fetched_total"] == 0

    def test_the_query_is_echoed_back_for_reproducibility(self, monkeypatch):
        for module in (track.arxiv, track.biorxiv, track.pubmed):
            monkeypatch.setattr(module, "search", lambda **kw: [])
        report = track.build_report(self._args(no_crossref=True, keywords=["cryo-EM"]))
        assert report["query"]["since"] == "2026-07-23"
        assert report["query"]["keywords"] == ["cryo-EM"]
        assert report["query"]["arxiv_categories"], "the q-bio default should be recorded"

    def test_duplicates_across_sources_are_merged_in_the_report(self, monkeypatch):
        title = "Cryo-EM structure of a bacterial multidrug efflux transporter"
        monkeypatch.setattr(
            track.arxiv, "search", lambda **kw: [self._paper("arxiv", "10.1/a", title)]
        )
        monkeypatch.setattr(
            track.biorxiv, "search", lambda **kw: [self._paper("biorxiv", "10.2/b", title)]
        )
        monkeypatch.setattr(track.pubmed, "search", lambda **kw: [])

        report = track.build_report(self._args(no_crossref=True))

        assert report["stats"]["fetched_total"] == 2
        assert report["stats"]["unique_total"] == 1
        assert report["stats"]["merges_by_tier"] == {"title-fingerprint": 1}


class TestMain:
    def test_an_inverted_window_exits_with_a_usage_error(self, capsys):
        assert track.main(["--since", "2026-07-30", "--until", "2026-07-01"]) == 2
        assert "is after" in capsys.readouterr().err

"""Dedup tiers, offline. Tier 2 is exercised with a stubbed Crossref."""

from datetime import date

import dedup
import pytest
from models import Paper


def paper(source, doi, title, *, year=2026, authors=("Zhang, Wei",), published_doi="", pid=None):
    return Paper(
        paper_id=pid or doi or title[:12],
        title=title,
        authors=list(authors),
        abstract=f"abstract of {title}",
        doi=doi,
        published_date=date(year, 6, 1),
        url=f"https://example.org/{doi or title[:8]}",
        pdf_url=f"https://example.org/{doi or title[:8]}.pdf" if source != "pubmed" else "",
        source=source,
        extra={"published_doi": published_doi} if published_doi else {},
    )


LONG_TITLE = "Cryo-EM structure of a bacterial multidrug efflux transporter"


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "10.1101/2026.01.02.123456",
            "https://doi.org/10.1101/2026.01.02.123456",
            "https://dx.doi.org/10.1101/2026.01.02.123456",
            "doi:10.1101/2026.01.02.123456",
            "  10.1101/2026.01.02.123456/ ",
            "10.1101/2026.01.02.123456".upper(),
        ],
    )
    def test_resolver_prefixes_and_case_fold_away(self, raw):
        assert dedup.normalise_doi(raw) == "10.1101/2026.01.02.123456"

    def test_title_fingerprint_ignores_punctuation_and_case(self):
        assert dedup.title_fingerprint("Cryo-EM: A Structure!") == dedup.title_fingerprint(
            "cryo em a structure"
        )


class TestTiers:
    def test_tier0_merges_identical_dois(self):
        merged, stats = deduplicate_offline(
            [
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE),
                paper("arxiv", "10.1016/J.CELL.2026.01.001", LONG_TITLE + " (v2)"),
            ]
        )
        assert len(merged) == 1
        assert stats.merges_by_tier == {"exact-doi": 1}

    def test_tier1_uses_the_biorxiv_published_field(self):
        merged, stats = deduplicate_offline(
            [
                paper(
                    "biorxiv",
                    "10.64898/2026.01.02.123456",
                    LONG_TITLE,
                    year=2025,
                    published_doi="10.1016/j.cell.2026.01.001",
                ),
                paper("pubmed", "10.1016/j.cell.2026.01.001", "A different wording entirely"),
            ]
        )
        assert len(merged) == 1
        assert stats.merges_by_tier == {"biorxiv-published": 1}

    def test_tier3_merges_on_title_when_dois_differ(self):
        merged, stats = deduplicate_offline(
            [
                paper("biorxiv", "10.64898/2026.01.02.123456", LONG_TITLE, year=2025),
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE + "!", year=2026),
            ]
        )
        assert len(merged) == 1
        assert stats.merges_by_tier == {"title-fingerprint": 1}

    def test_tier2_consults_crossref_for_unmatched_records(self, monkeypatch):
        monkeypatch.setattr(
            dedup,
            "_crossref_counterpart",
            lambda doi: "10.1016/j.cell.2026.01.001"
            if doi == "10.64898/2026.01.02.123456"
            else "",
        )
        merged, stats = dedup.deduplicate(
            [
                paper("biorxiv", "10.64898/2026.01.02.123456", "One phrasing of the work here"),
                paper("pubmed", "10.1016/j.cell.2026.01.001", "Quite another phrasing entirely"),
            ]
        )
        assert len(merged) == 1
        assert stats.merges_by_tier == {"crossref-relation": 1}
        assert stats.crossref_lookups >= 1

    def test_tier2_is_skipped_for_a_single_source_result_set(self, monkeypatch):
        """A counterpart can only merge if the other record is already present."""
        monkeypatch.setattr(
            dedup,
            "_crossref_counterpart",
            lambda doi: pytest.fail("Crossref must not be consulted for one source"),
        )
        _, stats = dedup.deduplicate(
            [paper("biorxiv", f"10.64898/2026.01.0{i}.1234", f"{LONG_TITLE} {i}") for i in range(4)]
        )
        assert stats.crossref_lookups == 0


class TestGuards:
    def test_short_titles_never_fingerprint_match(self):
        merged, _ = deduplicate_offline(
            [
                paper("arxiv", "", "Erratum"),
                paper("biorxiv", "", "Erratum"),
            ]
        )
        assert len(merged) == 2, "short titles collide across unrelated papers"

    def test_distant_years_are_treated_as_coincidence(self):
        merged, _ = deduplicate_offline(
            [
                paper("arxiv", "10.48550/arXiv.2001.00001", LONG_TITLE, year=2014),
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE, year=2026),
            ]
        )
        assert len(merged) == 2

    def test_different_first_authors_are_not_merged(self):
        merged, _ = deduplicate_offline(
            [
                paper("arxiv", "10.48550/arXiv.2601.1", LONG_TITLE, authors=("Zhang, Wei",)),
                paper("pubmed", "10.1016/j.cell.1", LONG_TITLE, authors=("Okafor, Ada",)),
            ]
        )
        assert len(merged) == 2

    def test_empty_input_is_not_an_error(self):
        merged, stats = dedup.deduplicate([], use_crossref=False)
        assert merged == []
        assert stats.papers_in == stats.papers_out == 0


class TestMergedRecord:
    def test_journal_version_wins_and_absorbs_the_preprint(self):
        merged, _ = deduplicate_offline(
            [
                paper("biorxiv", "10.64898/2026.01.02.123456", LONG_TITLE, year=2025),
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE, year=2026),
            ]
        )
        (record,) = merged
        assert record.source == "pubmed", "the version of record should lead"
        assert [a["source"] for a in record.also_in] == ["biorxiv"]
        assert record.merge_reason == "title-fingerprint"

    def test_missing_fields_are_backfilled_from_the_duplicate(self):
        """PubMed has no PDF link; its bioRxiv twin does. Keep it."""
        merged, _ = deduplicate_offline(
            [
                paper("biorxiv", "10.64898/2026.01.02.123456", LONG_TITLE, year=2025),
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE, year=2026),
            ]
        )
        assert merged[0].pdf_url.endswith(".pdf")

    def test_three_way_group_collapses_to_one(self):
        merged, _ = deduplicate_offline(
            [
                paper("arxiv", "10.48550/arXiv.2601.1", LONG_TITLE, year=2025),
                paper(
                    "biorxiv",
                    "10.64898/2026.01.02.123456",
                    LONG_TITLE,
                    year=2025,
                    published_doi="10.1016/j.cell.2026.01.001",
                ),
                paper("pubmed", "10.1016/j.cell.2026.01.001", LONG_TITLE, year=2026),
            ]
        )
        assert len(merged) == 1
        assert len(merged[0].also_in) == 2


def deduplicate_offline(papers):
    return dedup.deduplicate(papers, use_crossref=False)

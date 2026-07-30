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


class TestSurnameExtraction:
    """Getting this wrong silently disables tier 3 for a whole source."""

    @pytest.mark.parametrize(
        ("author", "expected"),
        [
            ("Falzone, M.", "falzone"),  # bioRxiv: Surname, Given
            ("Falzone ME", "falzone"),  # PubMed: Surname Initials
            ("Zhang W", "zhang"),
            ("Li J", "li"),
            ("Wei Zhang", "zhang"),  # arXiv: Given Surname
            ("John A. Smith", "smith"),
            ("van der Waals, J.", "vanderwaals"),
            ("", ""),
        ],
    )
    def test_every_source_name_order_yields_the_surname(self, author, expected):
        assert dedup._surname(paper("x", "", "t", authors=(author,))) == expected

    def test_the_same_author_matches_across_sources(self):
        """The bug this guards: 'Falzone ME' bucketed on 'me', never merging."""
        biorxiv_form = dedup._surname(paper("biorxiv", "", "t", authors=("Falzone, M.",)))
        pubmed_form = dedup._surname(paper("pubmed", "", "t", authors=("Falzone ME",)))
        assert biorxiv_form == pubmed_form == "falzone"

    def test_a_preprint_and_its_pubmed_record_merge_on_title(self):
        """End-to-end regression for the PLCβ pair that appeared twice."""
        title = "PLCbetas are recruited to the plasma membrane in macrophages"
        merged, stats = deduplicate_offline(
            [
                paper("pubmed", "10.1073/pnas.1", title, authors=("Falzone ME",), year=2026),
                paper("biorxiv", "10.64898/2026.01.28.702352", title,
                      authors=("Falzone, M.",), year=2026),
            ]
        )
        assert len(merged) == 1, "tier 3 must bridge PubMed and bioRxiv name formats"
        assert stats.merges_by_tier == {"title-fingerprint": 1}


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

    def test_crossref_runs_after_the_free_rules_not_before(self, monkeypatch):
        """It costs ~1.4s a lookup; the free title match should shrink its work."""
        looked_up = []
        monkeypatch.setattr(
            dedup, "_crossref_counterpart", lambda doi: looked_up.append(doi) or ""
        )
        dedup.deduplicate(
            [
                # This pair matches on title for free — Crossref must not see it.
                paper("biorxiv", "10.64898/a", LONG_TITLE, year=2025),
                paper("pubmed", "10.1016/a", LONG_TITLE, authors=("Zhang W",), year=2026),
                # This one has no free match, so it is a legitimate lookup.
                paper("arxiv", "10.48550/b", "An unrelated paper about something else"),
            ]
        )
        assert "10.64898/a" not in looked_up
        assert "10.1016/a" not in looked_up
        assert "10.48550/b" in looked_up

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


class TestCrossrefBudget:
    """216 lookups once bought zero merges. Spend the budget where it can pay."""

    def test_journal_records_are_looked_up_before_fresh_preprints(self):
        fresh = paper("biorxiv", "10.64898/new", "t")
        fresh.extra["version"] = "1"
        revised = paper("biorxiv", "10.64898/rev", "t")
        revised.extra["version"] = "3"
        journal = paper("pubmed", "10.1016/j", "t")

        ranked = sorted([fresh, revised, journal], key=dedup._crossref_priority)
        assert [p.source for p in ranked] == ["pubmed", "biorxiv", "biorxiv"]
        assert ranked[1].extra["version"] == "3", "revisions outrank first versions"

    def test_arxiv_version_is_read_from_the_id_suffix(self):
        v1 = paper("arxiv", "10.48550/a", "t")
        v1.extra["arxiv_id_versioned"] = "2601.01234v1"
        v2 = paper("arxiv", "10.48550/b", "t")
        v2.extra["arxiv_id_versioned"] = "2601.05678v2"
        assert dedup._crossref_priority(v1) == 2
        assert dedup._crossref_priority(v2) == 1

    def test_a_tight_budget_is_spent_on_the_best_candidates(self, monkeypatch):
        looked_up = []
        monkeypatch.setattr(
            dedup, "_crossref_counterpart", lambda doi: looked_up.append(doi) or ""
        )
        fresh = [paper("biorxiv", f"10.64898/{i}", f"Fresh preprint number {i}") for i in range(5)]
        for p in fresh:
            p.extra["version"] = "1"
        journal = paper("pubmed", "10.1016/j.cell.1", "A journal article about something")

        _, stats = dedup.deduplicate([*fresh, journal], max_crossref_lookups=1)

        assert looked_up == ["10.1016/j.cell.1"], "the single request went to a fresh preprint"
        assert stats.crossref_skipped == 5

    def test_a_large_budget_still_reaches_everything(self, monkeypatch):
        """Ordering defers low-yield lookups; it must not exclude them."""
        monkeypatch.setattr(dedup, "_crossref_counterpart", lambda doi: "")
        fresh = paper("biorxiv", "10.64898/x", "A fresh preprint posted this week")
        fresh.extra["version"] = "1"
        _, stats = dedup.deduplicate(
            [fresh, paper("pubmed", "10.1016/y", "An unrelated journal article")],
            max_crossref_lookups=100,
        )
        assert stats.crossref_lookups == 2
        assert stats.crossref_skipped == 0


class TestKeywordSignal:
    def test_the_keyword_flag_survives_a_merge(self):
        """It arrives on the Europe PMC record but the direct record wins."""
        title = "Recovery of a minor cryo-EM particle population"
        direct = paper("biorxiv", "10.64898/x", title)
        via_keywords = paper("europepmc", "10.64898/x", title)
        via_keywords.extra["keyword_match"] = True

        merged, _ = deduplicate_offline([direct, via_keywords])

        assert len(merged) == 1
        assert merged[0].source == "biorxiv", "the direct record is richer"
        assert merged[0].extra["keyword_match"] is True, "the signal must not die in the merge"

    def test_records_without_the_flag_stay_unflagged(self):
        merged, _ = deduplicate_offline([paper("biorxiv", "10.64898/y", "Some other preprint")])
        assert "keyword_match" not in merged[0].extra


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

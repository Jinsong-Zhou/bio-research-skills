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


class TestMisMergeGuards:
    """Reporting two different papers as one is the worst thing this can do.

    Each guard below closes a path that produced a real false merge when the
    dedup rules were audited: an unvalidated third-party DOI, a bucket key with
    half of it missing, and a year window that only checked adjacent hops.
    """

    def test_a_garbled_published_field_cannot_merge_across_a_decade(self):
        """Rule 2 acts on one third-party string with nothing corroborating it."""
        preprint = paper("biorxiv", "10.64898/x", "A 2026 cryo-EM study of transporters",
                         year=2026, published_doi="10.1016/old")
        unrelated = paper("pubmed", "10.1016/old", "Soil microbiome diversity in 2011",
                          year=2011, authors=("Okafor, A.",))

        merged, stats = dedup.deduplicate([preprint, unrelated], use_crossref=False)

        assert len(merged) == 2, "fifteen years apart is data corruption, not a match"
        assert stats.merges_by_tier == {}

    def test_a_plausible_publication_gap_still_merges(self):
        """The bound must not be so tight it rejects a slow journal."""
        preprint = paper("biorxiv", "10.64898/y", LONG_TITLE, year=2022,
                         published_doi="10.1016/j.cell.9")
        journal = paper("pubmed", "10.1016/j.cell.9", LONG_TITLE, year=2026)

        merged, stats = dedup.deduplicate([preprint, journal], use_crossref=False)

        assert len(merged) == 1
        assert "biorxiv-published" in merged[0].merge_reason

    def test_author_less_records_do_not_pool_on_a_shared_boilerplate_title(self):
        """With no surname the bucket key is the title alone.

        Conference abstract collections share one long title across unrelated
        records, and clear MIN_TITLE_CHARS comfortably.
        """
        shared = "Abstracts of the Annual Meeting of the Society for Neuroscience"
        one = paper("biorxiv", "10.64898/a1", shared, authors=())
        two = paper("biorxiv", "10.64898/a2", shared, authors=())

        merged, _ = dedup.deduplicate([one, two], use_crossref=False)

        assert len(merged) == 2

    def test_the_year_window_bounds_the_group_not_each_hop(self):
        """Union is transitive: 2019-2022 and 2022-2025 must not chain.

        Each hop clears a 3-year window while the endpoints are six years
        apart — an annually reissued database paper is exactly this shape.
        """
        editions = [
            paper("biorxiv", f"10.64898/e{y}", LONG_TITLE, year=y) for y in (2019, 2022, 2025)
        ]

        merged, _ = dedup.deduplicate(editions, use_crossref=False, year_window=3)

        spans = [
            max(p.published_date.year for p in group) - min(p.published_date.year for p in group)
            for group in [[m] for m in merged]
        ]
        assert all(span <= 3 for span in spans)
        assert len(merged) >= 2, "2019 and 2025 ended up in one group"

    def test_a_merge_is_auditable_from_the_output_alone(self):
        """also_in carries the losing record's title, or a bad merge is invisible."""
        preprint = paper("biorxiv", "10.64898/z", LONG_TITLE)
        journal = paper("pubmed", "10.1016/j.cell.7", LONG_TITLE)

        merged, _ = dedup.deduplicate([preprint, journal], use_crossref=False)

        (survivor,) = merged
        assert survivor.also_in[0]["title"] == LONG_TITLE
        assert survivor.also_in[0]["source"] == "biorxiv"


class TestCrossrefPayloadParsing:
    """_crossref_counterpart's JSON walk, which every other test stubs away.

    A typo in one of these keys disables rule 4 entirely: exit 0, empty
    `errors`, well-formed JSON, no merges.
    """

    def _payload(self, monkeypatch, payload):
        monkeypatch.setattr(dedup, "fetch_json", lambda url, *a, **k: payload)

    def test_the_preprint_side_relation_is_read(self, monkeypatch):
        self._payload(monkeypatch, {
            "message": {"relation": {"is-preprint-of": [{"id-type": "doi", "id": "10.1016/x"}]}}
        })
        assert dedup._crossref_counterpart("10.64898/a") == "10.1016/x"

    def test_the_journal_side_relation_is_read(self, monkeypatch):
        self._payload(monkeypatch, {
            "message": {"relation": {"has-preprint": [{"id-type": "doi", "id": "10.64898/A"}]}}
        })
        assert dedup._crossref_counterpart("10.1016/x") == "10.64898/a", "and normalised"

    def test_a_non_doi_identifier_is_ignored(self, monkeypatch):
        self._payload(monkeypatch, {
            "message": {"relation": {"is-preprint-of": [{"id-type": "uri", "id": "http://x"}]}}
        })
        assert dedup._crossref_counterpart("10.64898/a") == ""

    @pytest.mark.parametrize("payload", [
        {},
        {"message": None},
        {"message": {"relation": None}},
        {"message": {"relation": {"is-preprint-of": "not-a-list"}}},
        {"message": {"relation": {"is-preprint-of": ["not-a-dict"]}}},
        [1, 2, 3],
    ], ids=["empty", "null-message", "null-relation", "string-entries", "string-entry", "list"])
    def test_a_malformed_payload_yields_nothing_rather_than_killing_the_run(
        self, monkeypatch, payload
    ):
        """`.get(k, {})` returns the default only when the key is *absent*.

        A present-but-null value hands back None and the next .get raises —
        which used to abort a run that had already spent minutes fetching, with
        no JSON on stdout at all.
        """
        self._payload(monkeypatch, payload)
        assert dedup._crossref_counterpart("10.64898/a") == ""

    def test_the_doi_is_escaped_into_the_url(self, monkeypatch):
        """Legacy Wiley DOIs carry <, > and ;. A # or ? would truncate the path."""
        seen = {}
        monkeypatch.setattr(
            dedup, "fetch_json", lambda url, *a, **k: seen.update(url=url) or {"message": {}}
        )
        dedup._crossref_counterpart("10.1002/(sici)1097-0258(19980815)17:15<1661::aid-sim968>3.0.co;2-2")
        assert "<" not in seen["url"] and ">" not in seen["url"]
        assert seen["url"].startswith(dedup.CROSSREF_URL + "/")


class TestCrossrefBudgetIsARealCeiling:
    def test_failed_lookups_consume_the_budget(self, monkeypatch):
        """Charging only for successes turns the ceiling into no ceiling.

        Crossref 404s every arXiv DOI (those are DataCite) and 503s under load,
        so an outage would walk every candidate at three retries apiece while
        `crossref_skipped` stayed at 0.
        """
        attempts = []

        def always_fails(doi):
            attempts.append(doi)
            raise dedup.FetchError("Crossref is down")

        monkeypatch.setattr(dedup, "_crossref_counterpart", always_fails)
        papers = [paper("biorxiv", f"10.64898/{i}", f"Study number {i} of many") for i in range(20)]
        papers.append(paper("pubmed", "10.1016/j.cell.1", "An unrelated journal article"))

        _, stats = dedup.deduplicate(papers, max_crossref_lookups=5)

        assert len(attempts) == 5, f"budget of 5 allowed {len(attempts)} requests"
        assert stats.crossref_lookups == 5
        assert stats.crossref_failures == 5
        assert stats.crossref_skipped == 16, "the rest must be reported as skipped, not silent"

    def test_records_merged_mid_loop_are_not_looked_up_again(self, monkeypatch):
        """A stale group-size snapshot pays twice for one pair."""
        looked_up = []
        monkeypatch.setattr(
            dedup,
            "_crossref_counterpart",
            lambda doi: looked_up.append(doi) or ("10.64898/p" if doi == "10.1016/j" else ""),
        )
        pair = [
            paper("pubmed", "10.1016/j", "A journal article about transporters"),
            paper("biorxiv", "10.64898/p", "A preprint retitled before publication"),
        ]

        _, stats = dedup.deduplicate(pair, max_crossref_lookups=60)

        assert looked_up == ["10.1016/j"], f"asked about both directions: {looked_up}"
        assert stats.rule_matches.get("crossref-relation") == 1


class TestStatsTellTheTruth:
    def test_rule_matches_counts_every_agreement_not_just_new_unions(self):
        """A rule that agrees with a cheaper one still fired.

        SKILL.md tells the reader to judge a rule by rule_matches; merges_by_tier
        counts only unions it created, so a working rule can read as dead.
        """
        both = [
            paper("biorxiv", "10.64898/same", LONG_TITLE),
            paper("europepmc", "10.64898/same", LONG_TITLE),
        ]

        _, stats = dedup.deduplicate(both, use_crossref=False)

        assert stats.merges_by_tier == {"exact-doi": 1}
        assert stats.rule_matches == {"exact-doi": 1, "title-fingerprint": 1}

    def test_merge_reason_joins_every_rule_that_agreed(self):
        """The value is a + -joined set; matching it by equality misses this."""
        both = [
            paper("biorxiv", "10.64898/same", LONG_TITLE),
            paper("europepmc", "10.64898/same", LONG_TITLE),
        ]
        merged, _ = dedup.deduplicate(both, use_crossref=False)
        assert merged[0].merge_reason == "exact-doi+title-fingerprint"

    def test_an_unknown_source_is_named_rather_than_silently_demoted(self):
        odd = paper("chemrxiv", "10.26434/x", "A chemistry preprint with a long title")
        _, stats = dedup.deduplicate([odd], use_crossref=False)
        assert stats.unknown_sources == ["chemrxiv"]


class TestMergedRecordKeepsWhatMatters:
    def test_a_dateless_primary_inherits_its_twins_date(self):
        """PubMed outranks every preprint source but can carry no usable date.

        A <MedlineDate> range like "2026 Jul-Aug" parses to nothing, and without
        a backfill the merged record sorts to the bottom and shows up dateless
        while the real date sits in also_in.
        """
        journal = paper("pubmed", "10.1016/j.cell.5", LONG_TITLE)
        journal.published_date = None
        preprint = paper("biorxiv", "10.64898/w", LONG_TITLE, year=2026)

        merged, _ = dedup.deduplicate([journal, preprint], use_crossref=False)

        (survivor,) = merged
        assert survivor.source == "pubmed"
        assert survivor.published_date == date(2026, 6, 1)

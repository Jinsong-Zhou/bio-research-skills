"""Cross-source deduplication, including preprint to journal-version merging.

The hard case is not "the same record fetched twice" — matching DOIs handles
that. It is *the same work under two different DOIs*: a bioRxiv preprint and
the journal article it became. Nothing in the surveyed ecosystem merges those;
every tool we looked at keys on an exact DOI or an exact title string, so the
paper shows up twice.

No DOI prefix is hardcoded anywhere here, deliberately: bioRxiv has issued at
least two (``10.1101/`` historically, ``10.64898/`` on records seen in 2026),
so prefix sniffing would quietly stop working.

Four rules, applied cheapest first so the one that costs network requests only
sees what the free ones could not match:

=====  ==========================================  ==============================
Order  Rule                                        Cost
=====  ==========================================  ==============================
1      Identical normalised DOI                    free
2      bioRxiv/medRxiv ``published`` field         free (already in the record)
3      Title fingerprint + first author + year     free
4      Crossref ``is-preprint-of``/``has-preprint``  ~1.4s per unmatched DOI
=====  ==========================================  ==============================

Crossref runs last on purpose. It is the only rule that catches a paper
retitled between preprint and publication, but at roughly 1.4 seconds a lookup
it dominates the runtime — so it should only ever see the residue. Running it
before the free title match wastes requests on pairs already solved.

The title rule is deliberately conservative: short titles are skipped
entirely, and a first-author surname plus a year window must agree. Merging two
distinct papers is worse than showing one twice.

Acknowledgements
    Tier 0 mirrors ``_paper_unique_key`` from openags/paper-search-mcp (MIT).
    The tier-3 fingerprint follows RainerSeventeen/paper-tracker's
    ``core/dedup.py`` (MIT) — normalised DOI, title+author+year fingerprint,
    minimum-length guard, source-rank primary selection.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from models import Paper
from sources._http import FetchError, fetch_json

CROSSREF_URL = "https://api.crossref.org/works"

#: Lower rank wins the "primary record" slot. A journal article is the version
#: of record; the preprint is kept alongside it in ``also_in``. Europe PMC sits
#: last because it mirrors the preprint servers — when both are present the
#: direct record is richer (PDF link, subject area, version).
SOURCE_RANK = {"pubmed": 0, "biorxiv": 1, "medrxiv": 1, "arxiv": 2, "europepmc": 3}

#: Sources that publish preprints rather than versions of record.
PREPRINT_SOURCES = frozenset({"biorxiv", "medrxiv", "arxiv", "europepmc"})

#: Titles shorter than this are never fingerprint-matched. "Introduction" and
#: "Supplementary Material" collide across unrelated papers.
MIN_TITLE_CHARS = 24

#: A preprint and its journal version can be years apart; beyond this we treat
#: a fingerprint collision as coincidence.
DEFAULT_YEAR_WINDOW = 3

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_doi(raw: str) -> str:
    """Strip resolver prefixes and case so DOI strings compare equal."""
    return _DOI_PREFIX.sub("", (raw or "").strip()).strip().rstrip("/").lower()


def title_fingerprint(title: str) -> str:
    """Reduce a title to letters and digits, so punctuation and case stop mattering."""
    return _NON_ALNUM.sub("", (title or "").lower())


def _looks_like_initials(token: str) -> bool:
    """True for PubMed's trailing initials block: 'ME', 'JA', 'W'."""
    letters = token.replace(".", "")
    return 1 <= len(letters) <= 3 and letters.isalpha() and letters.isupper()


def _surname(paper: Paper) -> str:
    """First author's surname, normalised. Empty when unavailable.

    The three sources disagree on name order, and getting this wrong quietly
    disables tier 3: bucketing on the *initials* rather than the surname puts a
    PubMed record and its preprint in different buckets, so they never merge.

    ==============================  ==============  =========
    Format                          Example         Surname
    ==============================  ==============  =========
    bioRxiv ``Surname, Given``      Falzone, M.     Falzone
    PubMed ``Surname Initials``     Falzone ME      Falzone
    arXiv ``Given Surname``         Wei Zhang       Zhang
    ==============================  ==============  =========
    """
    if not paper.authors:
        return ""
    first = paper.authors[0].strip()
    if "," in first:
        surname = first.split(",")[0]
    else:
        tokens = first.split()
        if not tokens:
            surname = ""
        elif len(tokens) > 1 and _looks_like_initials(tokens[-1]):
            surname = tokens[0]  # PubMed order
        else:
            surname = tokens[-1]  # given-name-first order
    return _NON_ALNUM.sub("", surname.lower())


@dataclass
class MergeStats:
    """What deduplication actually did — surface this, never merge silently."""

    papers_in: int = 0
    papers_out: int = 0
    merges_by_tier: dict[str, int] = field(default_factory=dict)
    crossref_lookups: int = 0
    crossref_failures: int = 0
    crossref_skipped: int = 0

    @property
    def duplicates_removed(self) -> int:
        return self.papers_in - self.papers_out


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> bool:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return False
        self._parent[root_b] = root_a
        return True


def _crossref_counterpart(doi: str) -> str:
    """Return the DOI of ``doi``'s preprint or journal counterpart, if any.

    Crossref records the link from both directions: a preprint carries
    ``is-preprint-of``, the journal article carries ``has-preprint``.
    """
    payload = fetch_json(f"{CROSSREF_URL}/{doi}")
    relation = payload.get("message", {}).get("relation", {})
    for key in ("is-preprint-of", "has-preprint"):
        for entry in relation.get(key, []):
            if entry.get("id-type") == "doi" and entry.get("id"):
                return normalise_doi(entry["id"])
    return ""


def _crossref_priority(paper: Paper) -> int:
    """Expected payoff of a Crossref lookup — lower is spent first.

    A lookup only merges when the counterpart is already in the result set, so
    the odds differ sharply by what kind of record it is:

    0. **Journal articles.** Their preprint can be of any age, so it may well
       be in a window that also swept the preprint servers.
    1. **Revised preprints (v2+).** The original may long since have been
       published, and bioRxiv's ``published`` backfill sometimes lags.
    2. **First-version preprints.** A journal version would have to predate the
       preprint. Essentially never pays off — a measured run spent 216 lookups
       here for zero merges.

    This orders rather than excludes: with a large enough budget every record
    is still checked, so no merge is lost, only deferred.
    """
    if paper.source not in PREPRINT_SOURCES:
        return 0
    version = str(paper.extra.get("version", "")) or _arxiv_version(paper)
    return 1 if version not in ("", "1") else 2


def _arxiv_version(paper: Paper) -> str:
    """Version suffix from an arXiv id, e.g. '2601.01234v2' -> '2'."""
    versioned = str(paper.extra.get("arxiv_id_versioned", ""))
    _, _, suffix = versioned.rpartition("v")
    return suffix if suffix.isdigit() else ""


def _years_compatible(a: Paper, b: Paper, window: int) -> bool:
    if a.published_date is None or b.published_date is None:
        return True  # missing dates should not veto an otherwise strong match
    return abs(a.published_date.year - b.published_date.year) <= window


def _merge_group(members: list[Paper], reasons: set[str]) -> Paper:
    """Fold a group of duplicates into one record, keeping the richest fields."""
    members = sorted(members, key=lambda p: (SOURCE_RANK.get(p.source, 99), p.source))
    primary, rest = members[0], members[1:]

    # Backfill anything the primary lacks. PubMed records have no PDF link;
    # the bioRxiv twin does. Dropping that would lose real information.
    for other in rest:
        if not primary.abstract and other.abstract:
            primary.abstract = other.abstract
        if not primary.pdf_url and other.pdf_url:
            primary.pdf_url = other.pdf_url
        if not primary.doi and other.doi:
            primary.doi = other.doi
        primary.categories = list(dict.fromkeys(primary.categories + other.categories))
        primary.keywords = list(dict.fromkeys(primary.keywords + other.keywords))
        # Reaching a record through the keyword channel says something the
        # subject-area sweep cannot. Carry it to whichever record survives, or
        # the signal dies in the merge that produced it.
        if other.extra.get("keyword_match"):
            primary.extra["keyword_match"] = True

    primary.also_in = [
        {
            "source": other.source,
            "doi": other.doi,
            "url": other.url,
            "paper_id": other.paper_id,
            "published_date": other.published_date.isoformat() if other.published_date else "",
        }
        for other in rest
    ]
    primary.merge_reason = "+".join(sorted(reasons))
    return primary


def deduplicate(
    papers: list[Paper],
    *,
    use_crossref: bool = True,
    max_crossref_lookups: int = 60,
    year_window: int = DEFAULT_YEAR_WINDOW,
    min_title_chars: int = MIN_TITLE_CHARS,
) -> tuple[list[Paper], MergeStats]:
    """Merge records describing the same work across sources.

    Rules run in order and each union takes effect immediately, so Crossref —
    the only one that costs requests — sees only what the free rules missed.

    Args:
        use_crossref: run the Crossref rule. Roughly 1.4s per still-unmatched
            DOI; disable for a fully offline pass.
        max_crossref_lookups: hard ceiling on Crossref requests. Records skipped
            because of it are reported in ``MergeStats.crossref_skipped``.
        year_window: maximum year gap for a title-fingerprint match.
        min_title_chars: titles shorter than this skip the fingerprint rule.

    Returns:
        The merged papers, newest first, and the statistics describing what was
        merged and why.
    """
    stats = MergeStats(papers_in=len(papers))
    if not papers:
        return [], stats

    union = _DisjointSet(len(papers))
    group_reasons: dict[int, set[str]] = defaultdict(set)

    def link(a: int, b: int, reason: str) -> None:
        """Union two records and remember why, keeping reasons with the root."""
        root_a, root_b = union.find(a), union.find(b)
        carried = group_reasons.pop(root_a, set()) | group_reasons.pop(root_b, set())
        if union.union(a, b):
            stats.merges_by_tier[reason] = stats.merges_by_tier.get(reason, 0) + 1
        group_reasons[union.find(a)] = carried | {reason}

    # Tier 0 — identical DOI. Also builds the index the later tiers resolve into.
    doi_index: dict[str, int] = {}
    for i, paper in enumerate(papers):
        doi = normalise_doi(paper.doi)
        if not doi:
            continue
        if doi in doi_index:
            link(doi_index[doi], i, "exact-doi")
        else:
            doi_index[doi] = i

    # Tier 1 — bioRxiv/medRxiv already told us the journal DOI, for free.
    for i, paper in enumerate(papers):
        published = normalise_doi(paper.extra.get("published_doi", ""))
        if published and published in doi_index:
            link(i, doi_index[published], "biorxiv-published")

    # Rule 3 — title fingerprint, guarded by author surname and year distance.
    # Free, so it runs before Crossref and shrinks that rule's workload.
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, paper in enumerate(papers):
        fingerprint = title_fingerprint(paper.title)
        if len(fingerprint) < min_title_chars:
            continue
        buckets[(fingerprint, _surname(paper))].append(i)
    for members in buckets.values():
        for a, b in zip(members, members[1:]):
            if _years_compatible(papers[a], papers[b], year_window):
                link(a, b, "title-fingerprint")

    # Rule 4 — ask Crossref about records the free rules left standing alone.
    # Pointless with a single-source result set: a counterpart DOI can only be
    # merged if it is already in doi_index, which needs a second source. Without
    # this guard a bioRxiv-only run spends one request per paper for no merges.
    if use_crossref and len({p.source for p in papers}) > 1:
        group_sizes: dict[int, int] = defaultdict(int)
        for i in range(len(papers)):
            group_sizes[union.find(i)] += 1

        # Spend the budget where it can actually pay off. Without this the
        # order is arbitrary and a set dominated by fresh preprints burns every
        # request on records that cannot have a journal counterpart yet.
        candidates = sorted(
            (i for i in range(len(papers)) if normalise_doi(papers[i].doi)),
            key=lambda i: _crossref_priority(papers[i]),
        )

        for i in candidates:
            paper = papers[i]
            doi = normalise_doi(paper.doi)
            if group_sizes[union.find(i)] > 1:
                continue
            if stats.crossref_lookups >= max_crossref_lookups:
                stats.crossref_skipped += 1
                continue
            try:
                counterpart = _crossref_counterpart(doi)
                stats.crossref_lookups += 1
            except FetchError:
                stats.crossref_failures += 1
                continue
            if counterpart and counterpart in doi_index:
                link(i, doi_index[counterpart], "crossref-relation")

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(papers)):
        groups[union.find(i)].append(i)

    merged = [
        _merge_group([papers[i] for i in members], group_reasons[root])
        if len(members) > 1
        else papers[members[0]]
        for root, members in groups.items()
    ]
    merged.sort(key=lambda p: (p.published_date or date.min), reverse=True)

    stats.papers_out = len(merged)
    return merged, stats

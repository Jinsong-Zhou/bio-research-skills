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
sees what the free ones could not match. They are numbered 1-4 throughout —
in the code, the stats keys and the docs — with no zero-indexed variant:

====  ===========================================  =============================
Rule  Matches on                                   Cost
====  ===========================================  =============================
1     Identical normalised DOI                     free
2     bioRxiv/medRxiv ``published`` field          free (already in the record)
3     Title fingerprint + first author + year      free
4     Crossref ``is-preprint-of``/``has-preprint``  ~1.4s per unmatched DOI
====  ===========================================  =============================

Crossref runs last on purpose. It is the only rule that catches a paper
retitled between preprint and publication, but at roughly 1.4 seconds a lookup
it dominates the runtime — so it should only ever see the residue. Running it
before the free title match wastes requests on pairs already solved.

The title rule is deliberately conservative: short titles are skipped
entirely, and a first-author surname plus a year window must agree. Merging two
distinct papers is worse than showing one twice.

Acknowledgements
    Rule 1 mirrors ``_paper_unique_key`` from openags/paper-search-mcp (MIT).
    The rule 3 fingerprint follows RainerSeventeen/paper-tracker's
    ``core/dedup.py`` (MIT) — normalised DOI, title+author+year fingerprint,
    minimum-length guard, source-rank primary selection.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

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

#: Sanity bound on rule 2, which acts on bioRxiv's ``published`` field alone.
#: Wider than the fingerprint window because the signal is much stronger — a
#: slow journal really can take four years — but not unbounded, so a garbled
#: field cannot merge a 2026 preprint with a 2011 paper.
PUBLISHED_FIELD_YEAR_WINDOW = 6

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
    #: New unions created, by rule. Sums to ``duplicates_removed``.
    merges_by_tier: dict[str, int] = field(default_factory=dict)
    #: Every time a rule matched a pair, whether or not it created a new union.
    #: A pair already merged by an earlier rule still counts here — without
    #: this, a rule that agrees with a cheaper one looks like it never fired.
    rule_matches: dict[str, int] = field(default_factory=dict)
    crossref_lookups: int = 0
    crossref_failures: int = 0
    crossref_skipped: int = 0
    #: Sources missing from ``SOURCE_RANK``. They sort below every known source
    #: and so quietly lose the primary slot to whatever they merged with —
    #: worth naming rather than absorbing into a default rank.
    unknown_sources: list[str] = field(default_factory=list)

    @property
    def duplicates_removed(self) -> int:
        return self.papers_in - self.papers_out


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

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
        self._size[root_a] += self._size[root_b]
        return True

    def group_size(self, item: int) -> int:
        """How many records are currently grouped with ``item``.

        Live, not snapshotted — a caller that caches this before a loop that
        keeps merging will re-examine records it has already paired up.
        """
        return self._size[self.find(item)]


def _crossref_counterpart(doi: str) -> str:
    """Return the DOI of ``doi``'s preprint or journal counterpart, if any.

    Crossref records the link from both directions: a preprint carries
    ``is-preprint-of``, the journal article carries ``has-preprint``.

    Every step is defensive about the payload's shape. ``.get(k, {})`` returns
    the default only when the key is *absent*: a present-but-null ``message``
    hands back ``None`` and the next ``.get`` raises. One malformed response
    would otherwise kill a run that had already spent minutes fetching.
    """
    # Quote the DOI: legacy Wiley-style DOIs carry '<', '>' and ';', and a '?'
    # or '#' would silently truncate the request path.
    payload = fetch_json(f"{CROSSREF_URL}/{quote(doi, safe='')}")
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    relation = message.get("relation") if isinstance(message, dict) else None
    if not isinstance(relation, dict):
        return ""
    for key in ("is-preprint-of", "has-preprint"):
        entries = relation.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id-type") == "doi" and entry.get("id"):
                return normalise_doi(str(entry["id"]))
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
       preprint. Essentially never pays off — a measured run with the budget
       raised to 250 spent 216 lookups here for zero merges. (The shipped
       default is 60, so that figure is not reproducible without ``--max-
       crossref-lookups``.)

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


def _year_clusters(papers: list[Paper], members: list[int], window: int) -> list[list[int]]:
    """Split a fingerprint bucket into runs spanning at most ``window`` years.

    Checking only adjacent pairs is not enough, because union is transitive:
    2019-2022 and 2022-2025 each clear a 3-year window, yet linking both puts
    records six years apart in one group. Anchoring each cluster on its
    earliest member bounds the whole group instead of each hop.
    """
    dated: list[tuple[date, int]] = []
    undated: list[int] = []
    for i in members:
        posted = papers[i].published_date
        if posted is None:
            undated.append(i)
        else:
            dated.append((posted, i))

    dated.sort()
    clusters: list[list[int]] = []
    anchors: list[int] = []
    for posted, i in dated:
        if clusters and posted.year - anchors[-1] <= window:
            clusters[-1].append(i)
        else:
            clusters.append([i])
            anchors.append(posted.year)

    # A missing date should not veto an otherwise strong match, so undated
    # records join the earliest cluster rather than forming one of their own.
    if undated and clusters:
        clusters[0].extend(undated)
    elif undated:
        clusters.append(undated)
    return clusters


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
        # PubMed outranks every preprint source but can carry no usable date at
        # all (a <MedlineDate> range like "2026 Jul-Aug" parses to nothing).
        # Without this the merged record sorts to the bottom and shows up
        # dateless, while the real date sits in its bioRxiv twin.
        if primary.published_date is None and other.published_date is not None:
            primary.published_date = other.published_date
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
            # Carried so a wrong merge is auditable from the output alone.
            # Without it the losing record's title is simply gone, and two
            # unrelated papers reported as one look no different from a
            # correct merge.
            "title": other.title,
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
    stats.unknown_sources = sorted({p.source for p in papers} - set(SOURCE_RANK))

    union = _DisjointSet(len(papers))
    group_reasons: dict[int, set[str]] = defaultdict(set)

    def link(a: int, b: int, reason: str) -> None:
        """Union two records and remember why, keeping reasons with the root."""
        root_a, root_b = union.find(a), union.find(b)
        carried = group_reasons.pop(root_a, set()) | group_reasons.pop(root_b, set())
        stats.rule_matches[reason] = stats.rule_matches.get(reason, 0) + 1
        if union.union(a, b):
            stats.merges_by_tier[reason] = stats.merges_by_tier.get(reason, 0) + 1
        group_reasons[union.find(a)] = carried | {reason}

    # Rule 1 — identical DOI. Also builds the index the later rules resolve into.
    doi_index: dict[str, int] = {}
    for i, paper in enumerate(papers):
        doi = normalise_doi(paper.doi)
        if not doi:
            continue
        if doi in doi_index:
            link(doi_index[doi], i, "exact-doi")
        else:
            doi_index[doi] = i

    # Rule 2 — bioRxiv/medRxiv already told us the journal DOI, for free.
    for i, paper in enumerate(papers):
        published = normalise_doi(paper.extra.get("published_doi", ""))
        if not published or published not in doi_index:
            continue
        # The one rule with no corroboration of its own: it acts on a single
        # third-party string. A generous year bound does not validate the
        # link, it just stops a stale or garbled `published` field from
        # merging records that cannot be the same work. Real preprint-to-
        # journal gaps run to a few years; fifteen is data corruption.
        if _years_compatible(paper, papers[doi_index[published]], PUBLISHED_FIELD_YEAR_WINDOW):
            link(i, doi_index[published], "biorxiv-published")

    # Rule 3 — title fingerprint, guarded by author surname and year distance.
    # Free, so it runs before Crossref and shrinks that rule's workload.
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, paper in enumerate(papers):
        fingerprint = title_fingerprint(paper.title)
        if len(fingerprint) < min_title_chars:
            continue
        surname = _surname(paper)
        if not surname:
            # Both halves of the key have to mean something. With no author the
            # bucket degenerates to the title alone, which pools every record
            # sharing a boilerplate one — "Abstracts of the Annual Meeting of
            # the ..." clears MIN_TITLE_CHARS comfortably.
            continue
        buckets[(fingerprint, surname)].append(i)
    for members in buckets.values():
        for cluster in _year_clusters(papers, members, year_window):
            for a, b in zip(cluster, cluster[1:]):
                link(a, b, "title-fingerprint")

    # Rule 4 — ask Crossref about records the free rules left standing alone.
    # Pointless with a single-source result set: a counterpart DOI can only be
    # merged if it is already in doi_index, which needs a second source. Without
    # this guard a bioRxiv-only run spends one request per paper for no merges.
    if use_crossref and len({p.source for p in papers}) > 1:
        # Spend the budget where it can actually pay off. Without this the
        # order is arbitrary and a set dominated by fresh preprints burns every
        # request on records that cannot have a journal counterpart yet.
        candidates = sorted(
            (i for i in range(len(papers)) if normalise_doi(papers[i].doi)),
            key=lambda i: _crossref_priority(papers[i]),
        )

        for i in candidates:
            doi = normalise_doi(papers[i].doi)
            # Live group size, not a snapshot: a record paired up earlier in
            # this very loop no longer needs asking about.
            if union.group_size(i) > 1:
                continue
            if stats.crossref_lookups >= max_crossref_lookups:
                stats.crossref_skipped += 1
                continue
            # Count the attempt, not the success. Crossref 404s every arXiv
            # DOI (those are DataCite) and 503s under load, and charging only
            # for successes turns the budget into no ceiling at all — an
            # outage would walk every candidate at three retries apiece.
            stats.crossref_lookups += 1
            try:
                counterpart = _crossref_counterpart(doi)
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

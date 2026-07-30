"""bioRxiv / medRxiv adapter.

Three things this fixes relative to naive use of the API:

1. **Category validation.** The API silently ignores an unknown ``category`` and
   returns *every* paper in the date window. A keyword accidentally passed as a
   category therefore yields plausible-looking but unrelated results. We
   validate against a whitelist and refuse rather than mislead.
2. **Real pagination, from the correct end.** Page size is whatever the API
   feels like (30 at the time of writing, not the 100 the cursor increments
   suggest), so we advance by the batch length we actually received and stop
   against ``messages[0].total``. Records come back **oldest first**, so a
   naive read of page 1 returns the *oldest* slice — the opposite of what a
   "what's new" query wants. We seek to the tail instead.
3. **Version collapsing.** The API returns one record per preprint *version*.
   We keep the newest version of each DOI.

The API has no keyword search at all — that is a genuine upstream limitation,
not an oversight here. Filter by category, then narrow downstream.

See ``references/source-quirks.md`` and ``references/biorxiv-categories.md``.
"""

from __future__ import annotations

import difflib
from datetime import date, datetime

from models import Paper

from ._http import fetch_json

BASE_URL = "https://api.biorxiv.org/details"

#: Only used to bound a runaway loop. The API decides the real page size and we
#: read it off each response — assuming a constant here is what silently caps a
#: query at one page.
MAX_PAGES = 200

SERVERS = ("biorxiv", "medrxiv")

#: Subject areas accepted by each server, as returned by the API. Kept as
#: canonical display strings; matching is case- and separator-insensitive.
#: ``tests/test_categories.py`` re-checks these against the live API.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "biorxiv": (
        "animal behavior and cognition",
        "biochemistry",
        "bioengineering",
        "bioinformatics",
        "biophysics",
        "cancer biology",
        "cell biology",
        "clinical trials",
        "developmental biology",
        "ecology",
        "epidemiology",
        "evolutionary biology",
        "genetics",
        "genomics",
        "immunology",
        "microbiology",
        "molecular biology",
        "neuroscience",
        "paleontology",
        "pathology",
        "pharmacology and toxicology",
        "physiology",
        "plant biology",
        "scientific communication and education",
        "synthetic biology",
        "systems biology",
        "zoology",
    ),
    "medrxiv": (
        "addiction medicine",
        "allergy and immunology",
        "anesthesia",
        "cardiovascular medicine",
        "dentistry and oral medicine",
        "dermatology",
        "emergency medicine",
        "endocrinology",
        "epidemiology",
        "forensic medicine",
        "gastroenterology",
        "genetic and genomic medicine",
        "geriatric medicine",
        "health economics",
        "health informatics",
        "health policy",
        "health systems and quality improvement",
        "hematology",
        "hiv aids",
        "infectious diseases",
        "intensive care and critical care medicine",
        "medical education",
        "medical ethics",
        "nephrology",
        "neurology",
        "nursing",
        "nutrition",
        "obstetrics and gynecology",
        "occupational and environmental health",
        "oncology",
        "ophthalmology",
        "orthopedics",
        "otolaryngology",
        "pain medicine",
        "palliative medicine",
        "pathology",
        "pediatrics",
        "pharmacology and therapeutics",
        "primary care research",
        "psychiatry and clinical psychology",
        "public and global health",
        "radiology and imaging",
        "rehabilitation medicine and physical therapy",
        "respiratory medicine",
        "rheumatology",
        "sexual and reproductive health",
        "sports medicine",
        "surgery",
        "toxicology",
        "transplantation",
        "urology",
    ),
}


class UnknownCategoryError(ValueError):
    """Guard against the API's silent ignore-and-return-everything behaviour."""


def _normalise(value: str) -> str:
    """Fold case and separators so 'Cell Biology' == 'cell_biology'."""
    return " ".join(value.replace("_", " ").replace("/", " ").lower().split())


def resolve_category(category: str, server: str) -> str:
    """Map a user-supplied category to the API's underscore form.

    Raises:
        UnknownCategoryError: with close-match suggestions. This is the whole
            point — an unvalidated category would return unrelated papers that
            look entirely legitimate.
    """
    known = CATEGORIES[server]
    target = _normalise(category)
    lookup = {_normalise(c): c for c in known}
    if target in lookup:
        return lookup[target].replace(" ", "_")

    suggestions = difflib.get_close_matches(target, lookup.keys(), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise UnknownCategoryError(
        f"{category!r} is not a {server} subject area — the API would silently "
        f"ignore it and return every paper in the window.{hint}"
    )


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_paper(item: dict, server: str) -> Paper:
    doi = item.get("doi", "")
    version = str(item.get("version", "1"))
    published_doi = (item.get("published") or "").strip()
    # The API writes the string "NA" when a preprint has no journal version.
    if published_doi.upper() == "NA":
        published_doi = ""

    return Paper(
        paper_id=doi,
        title=" ".join((item.get("title") or "").split()),
        authors=[a.strip() for a in (item.get("authors") or "").split(";") if a.strip()],
        abstract=" ".join((item.get("abstract") or "").split()),
        doi=doi,
        published_date=_parse_date(item.get("date", "")),
        url=f"https://www.{server}.org/content/{doi}v{version}",
        pdf_url=f"https://www.{server}.org/content/{doi}v{version}.full.pdf",
        source=server,
        categories=[item["category"]] if item.get("category") else [],
        extra={
            "version": version,
            # Populated by bioRxiv once the preprint appears in a journal —
            # this is dedup tier 1 (see dedup.py), free with the record.
            "published_doi": published_doi,
            "preprint_server": server,
        },
    )


def _collapse_versions(papers: list[Paper]) -> list[Paper]:
    """Keep only the newest version of each DOI."""
    best: dict[str, Paper] = {}
    for paper in papers:
        current = best.get(paper.doi)
        if current is None or int(paper.extra["version"]) > int(current.extra["version"]):
            best[paper.doi] = paper
    return list(best.values())


def _reported_total(payload: dict) -> int | None:
    """The window's true record count, from ``messages[0].total`` (a string)."""
    messages = payload.get("messages") or [{}]
    try:
        return int(messages[0]["total"])
    except (KeyError, TypeError, ValueError):
        return None


def _fetch_category(
    server: str, since: date, until: date, category: str | None, limit: int
) -> list[Paper]:
    """Fetch up to ``limit`` of the *newest* records in the window.

    The API paginates oldest-first, so once the window holds more than we want
    we seek to ``total - limit`` and read to the end rather than taking page 1.
    """
    window = f"{BASE_URL}/{server}/{since:%Y-%m-%d}/{until:%Y-%m-%d}"
    params = {"category": category}

    probe = fetch_json(f"{window}/0", params)
    first_batch = probe.get("collection", [])
    if not first_batch:
        return []

    total = _reported_total(probe)
    if total is None or total <= len(first_batch):
        return [_to_paper(item, server) for item in first_batch[:limit]]

    # Seek to the tail. Re-reading page 0 is only wasteful when the window is
    # small, and correctness beats saving one request.
    cursor = max(0, total - limit)
    papers: list[Paper] = []
    for _ in range(MAX_PAGES):
        if cursor >= total:
            break
        payload = fetch_json(f"{window}/{cursor}", params)
        batch = payload.get("collection", [])
        if not batch:
            break
        papers.extend(_to_paper(item, server) for item in batch)
        cursor += len(batch)  # never a hardcoded page size

    return papers[-limit:] if len(papers) > limit else papers


def search(
    *,
    since: date,
    until: date | None = None,
    categories: list[str] | None = None,
    server: str = "biorxiv",
    max_results: int = 500,
) -> list[Paper]:
    """Fetch preprints posted in ``[since, until]``, newest first.

    ``categories`` of ``None`` means every subject area. Each category costs a
    separate pass — the API accepts only one at a time — and gets an equal
    share of ``max_results``, so one busy area cannot crowd out the rest.

    Raises:
        UnknownCategoryError: an unrecognised subject area was requested.
        FetchError: transport failure that outlived the retries.
    """
    if server not in SERVERS:
        raise ValueError(f"server must be one of {SERVERS}, got {server!r}")
    until = until or date.today()
    if since > until:
        raise ValueError(f"since ({since}) is after until ({until})")

    # Resolve every category up front so a typo fails before any network I/O.
    resolved = [resolve_category(c, server) for c in categories] if categories else [None]
    per_category = max(1, max_results // len(resolved))

    collected: list[Paper] = []
    for category in resolved:
        collected.extend(_fetch_category(server, since, until, category, per_category))

    papers = _collapse_versions(collected)
    papers.sort(key=lambda p: (p.published_date or date.min), reverse=True)
    return papers[:max_results]

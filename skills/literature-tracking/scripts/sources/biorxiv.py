"""bioRxiv / medRxiv adapter.

Three things this fixes relative to naive use of the API:

1. **Category validation.** The API silently ignores an unknown ``category`` and
   returns *every* paper in the date window. A keyword accidentally passed as a
   category therefore yields plausible-looking but unrelated results. We
   validate against a whitelist and refuse rather than mislead.
2. **Real pagination.** ``messages[0].total`` gives the true count; the cursor
   advances by 100 until we have what we asked for.
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
PAGE_SIZE = 100

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
    separate pass, since the API accepts only one at a time.

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

    collected: list[Paper] = []
    for category in resolved:
        cursor = 0
        while len(collected) < max_results:
            payload = fetch_json(
                f"{BASE_URL}/{server}/{since:%Y-%m-%d}/{until:%Y-%m-%d}/{cursor}",
                {"category": category},
            )
            batch = payload.get("collection", [])
            if not batch:
                break
            collected.extend(_to_paper(item, server) for item in batch)
            if len(batch) < PAGE_SIZE:
                break
            cursor += PAGE_SIZE

    papers = _collapse_versions(collected)
    papers.sort(key=lambda p: (p.published_date or date.min), reverse=True)
    return papers[:max_results]

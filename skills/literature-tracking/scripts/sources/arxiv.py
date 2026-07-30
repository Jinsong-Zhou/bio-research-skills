"""arXiv adapter with real category and date-range filtering.

The arXiv API supports structured queries (``cat:``, ``submittedDate:[..]``),
but only if they are *not* wrapped in a field prefix. Wrapping them in ``all:``
— as some clients do — silently yields a single fake entry titled "Error"
under HTTP 200. ``_check_for_error_entry`` catches exactly that.

See ``references/source-quirks.md`` for the measured evidence.
"""

from __future__ import annotations

from datetime import date, datetime
from xml.etree import ElementTree as ET

from models import Paper, SearchResult

from ._http import SourceError, fetch_xml

BASE_URL = "https://export.arxiv.org/api/query"
PAGE_SIZE = 100
MAX_RESULTS_CAP = 2000  # arXiv rejects larger single requests

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

#: The quantitative-biology archive. Callers usually want all of it.
QBIO_CATEGORIES = (
    "q-bio.BM",  # Biomolecules
    "q-bio.CB",  # Cell Behavior
    "q-bio.GN",  # Genomics
    "q-bio.MN",  # Molecular Networks
    "q-bio.NC",  # Neurons and Cognition
    "q-bio.OT",  # Other
    "q-bio.PE",  # Populations and Evolution
    "q-bio.QM",  # Quantitative Methods
    "q-bio.SC",  # Subcellular Processes
    "q-bio.TO",  # Tissues and Organs
)


class ArxivQueryError(SourceError):
    """arXiv rejected the query and said so inside a 200 response."""


def _stamp(day: date, *, end_of_day: bool = False) -> str:
    return day.strftime("%Y%m%d") + ("2359" if end_of_day else "0000")


def _quote(term: str) -> str:
    """Multi-word terms must be quoted or arXiv splits them on whitespace."""
    return f'"{term}"' if " " in term.strip() else term.strip()


def build_query(
    keywords: list[str] | None = None,
    categories: list[str] | None = None,
    since: date | None = None,
    until: date | None = None,
) -> str:
    """Assemble a structured arXiv ``search_query``.

    Groups are ANDed; members within a group are ORed. Passing nothing is a
    programming error — an unbounded arXiv query returns the whole archive.
    """
    groups: list[str] = []
    if categories:
        groups.append("(" + " OR ".join(f"cat:{c}" for c in categories) + ")")
    if keywords:
        groups.append("(" + " OR ".join(f"all:{_quote(k)}" for k in keywords) + ")")
    if since or until:
        lo = _stamp(since) if since else "199101010000"
        hi = _stamp(until, end_of_day=True) if until else _stamp(date.today(), end_of_day=True)
        groups.append(f"submittedDate:[{lo} TO {hi}]")

    if not groups:
        raise ValueError("refusing to run an unbounded arXiv query")
    return " AND ".join(groups)


def _check_for_error_entry(root: ET.Element) -> None:
    """Raise if arXiv returned its HTTP-200 error document.

    The signature is a feed with exactly one entry whose title is "Error"; the
    human-readable reason sits in that entry's summary.
    """
    entries = root.findall("atom:entry", NS)
    if len(entries) != 1:
        return
    title = (entries[0].findtext("atom:title", default="", namespaces=NS) or "").strip()
    if title.lower() != "error":
        return
    reason = (entries[0].findtext("atom:summary", default="", namespaces=NS) or "").strip()
    raise ArxivQueryError(reason or "arXiv rejected the query without giving a reason")


def _text(entry: ET.Element, path: str) -> str:
    return " ".join((entry.findtext(path, default="", namespaces=NS) or "").split())


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        return None


def _parse_entry(entry: ET.Element) -> Paper:
    entry_id = _text(entry, "atom:id")
    pdf_url = ""
    for link in entry.findall("atom:link", NS):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href", "")
            break

    return Paper(
        # Strip the version suffix so v1 and v3 of one preprint share an id.
        paper_id=entry_id.rsplit("/", 1)[-1].split("v")[0] if entry_id else "",
        title=_text(entry, "atom:title"),
        authors=[
            " ".join((a.findtext("atom:name", default="", namespaces=NS) or "").split())
            for a in entry.findall("atom:author", NS)
        ],
        abstract=_text(entry, "atom:summary"),
        doi=_text(entry, "arxiv:doi"),
        published_date=_parse_date(_text(entry, "atom:published")),
        updated_date=_parse_date(_text(entry, "atom:updated")),
        url=entry_id,
        pdf_url=pdf_url,
        source="arxiv",
        categories=[c.get("term", "") for c in entry.findall("atom:category", NS)],
        extra={"arxiv_id_versioned": entry_id.rsplit("/", 1)[-1] if entry_id else ""},
    )


def _total_results(root: ET.Element) -> int | None:
    raw = root.findtext("opensearch:totalResults", namespaces=NS)
    return int(raw) if raw and raw.strip().isdigit() else None


def search(
    *,
    keywords: list[str] | None = None,
    categories: list[str] | None = None,
    since: date | None = None,
    until: date | None = None,
    max_results: int = 200,
) -> SearchResult:
    """Fetch arXiv papers matching the filters, newest submission first.

    Note that ANDing keywords onto a category filter is usually a mistake here:
    all of q-bio runs under a hundred submissions a week, small enough to rank
    by hand, and arXiv splits hyphenated terms even inside quotes. See
    ``references/source-quirks.md``.

    Raises:
        ArxivQueryError: the query was malformed (arXiv's 200-with-Error case).
        FetchError: transport failure that outlived the retries.
    """
    query = build_query(keywords, categories, since, until)
    wanted = min(max_results, MAX_RESULTS_CAP)

    papers: list[Paper] = []
    seen_ids: set[str] = set()
    available: int | None = None
    start = 0
    while len(papers) < wanted:
        root = fetch_xml(
            BASE_URL,
            {
                "search_query": query,
                "start": start,
                "max_results": min(PAGE_SIZE, wanted - len(papers)),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        _check_for_error_entry(root)
        if available is None:
            available = _total_results(root)

        entries = root.findall("atom:entry", NS)
        if not entries:
            break

        for entry in entries:
            paper = _parse_entry(entry)
            # arXiv occasionally repeats entries across page boundaries.
            if paper.paper_id and paper.paper_id not in seen_ids:
                seen_ids.add(paper.paper_id)
                papers.append(paper)

        if len(entries) < PAGE_SIZE:
            break
        start += len(entries)

    return SearchResult(papers[:wanted], available)

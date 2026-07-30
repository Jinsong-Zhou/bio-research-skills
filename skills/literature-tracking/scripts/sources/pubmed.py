"""PubMed adapter via NCBI E-utilities, with day-precision dates.

Two things worth knowing:

* PubMed's ``<PubDate>`` is often year-only (or a range like "2024 Jul-Aug"),
  which is useless for a "what appeared this week" query. We prefer the
  Entrez/PubMed history dates and the electronic ``<ArticleDate>``, both of
  which carry a day, and fall back to ``<PubDate>`` only as a last resort.
* ``esearch`` bounds the window server-side via ``mindate``/``maxdate`` plus
  ``datetype=edat`` (Entrez date = when PubMed indexed the record), so we never
  pull a year of results just to throw them away. **The Entrez date is not the
  publication date**: a paper published in April can be indexed in July, so a
  seven-day window legitimately returns records whose ``published_date`` is
  months old. Both are kept — ``published_date`` is when it was published,
  ``extra["entrez_date"]`` is the one that bounded the search — so a digest can
  say which it means instead of quietly contradicting its own window.

``NCBI_API_KEY`` is optional. It raises NCBI's *server-side* limit from 3 to 10
requests/second, but ``_http._HOST_INTERVAL`` paces this client to the
anonymous 3/s either way — so the key buys headroom against a ban, not speed.
Raise the interval too if you ever need the throughput.
"""

from __future__ import annotations

import calendar
import os
from datetime import date
from xml.etree import ElementTree as ET

from models import Paper, SearchResult

from ._http import fetch_json, fetch_xml

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

#: efetch accepts far more, but oversized id lists invite timeouts.
FETCH_BATCH = 200
SEARCH_BATCH = 500

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}


def _auth_params() -> dict[str, str]:
    key = os.environ.get("NCBI_API_KEY")
    return {"api_key": key} if key else {}


def _month(raw: str | None) -> int:
    if not raw:
        return 1
    raw = raw.strip()
    if raw.isdigit():
        return max(1, min(12, int(raw)))
    return _MONTHS.get(raw[:3].lower(), 1)


def _date_from(node: ET.Element | None) -> date | None:
    """Build a date from a Year/Month/Day element trio, tolerating gaps."""
    if node is None:
        return None
    year_text = node.findtext("Year")
    if not year_text or not year_text.strip().isdigit():
        return None
    year = int(year_text.strip())
    month = _month(node.findtext("Month"))
    day_text = (node.findtext("Day") or "1").strip()
    day = int(day_text) if day_text.isdigit() else 1
    try:
        return date(year, month, day)
    except ValueError:
        # e.g. "31" in a 30-day month. Clamp to the last real day rather than
        # dropping the record — falling back to the 1st would shift the date
        # backwards by most of a month and can push it outside the window.
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(day, last))


def _entrez_date(article: ET.Element) -> date | None:
    """When PubMed indexed the record — the date ``esearch`` filters on."""
    return _date_from(article.find('.//PubmedData/History/PubMedPubDate[@PubStatus="entrez"]'))


def _best_date(article: ET.Element) -> date | None:
    """Pick the most precise available date, preferring day-level sources."""
    # 1. Electronic publication date — has a real day when present.
    electronic = article.find(".//Article/ArticleDate")
    if (found := _date_from(electronic)) is not None:
        return found

    # 2. Entrez history dates — always day-level.
    for status in ("entrez", "pubmed", "medline"):
        node = article.find(f'.//PubmedData/History/PubMedPubDate[@PubStatus="{status}"]')
        if (found := _date_from(node)) is not None:
            return found

    # 3. Journal PubDate — frequently year-only, so it lands on 1 January.
    return _date_from(article.find(".//Journal/JournalIssue/PubDate"))


def _entrez_range(papers: list[Paper]) -> tuple[date, date] | None:
    """Span of the dates ``esearch`` actually filtered on.

    Reporting the publication-date span instead would answer a different
    question than the one the window asked: a seven-day Entrez window routinely
    holds papers published months earlier, so that span reads as though the
    sweep reached far outside its own window.
    """
    seen: list[date] = []
    for paper in papers:
        raw = str(paper.extra.get("entrez_date", ""))
        if not raw:
            continue
        try:
            seen.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return (min(seen), max(seen)) if seen else None


def _text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _parse_article(article: ET.Element) -> Paper | None:
    pmid = _text(article.find(".//MedlineCitation/PMID"))
    title = _text(article.find(".//Article/ArticleTitle"))
    if not pmid or not title:
        return None

    authors: list[str] = []
    for author in article.findall(".//Article/AuthorList/Author"):
        last = _text(author.find("LastName"))
        if not last:
            authors.append(_text(author.find("CollectiveName")))
            continue
        initials = _text(author.find("Initials"))
        authors.append(f"{last} {initials}".strip())
    authors = [a for a in authors if a]

    # Structured abstracts arrive as several labelled chunks.
    chunks = []
    for part in article.findall(".//Article/Abstract/AbstractText"):
        text = _text(part)
        if not text:
            continue
        label = part.get("Label")
        chunks.append(f"{label}: {text}" if label else text)
    abstract = " ".join(chunks)

    ids = {
        (node.get("IdType") or "").lower(): (node.text or "").strip()
        for node in article.findall(".//PubmedData/ArticleIdList/ArticleId")
    }
    doi = ids.get("doi") or _text(article.find('.//Article/ELocationID[@EIdType="doi"]'))
    pmcid = ids.get("pmc", "")

    return Paper(
        paper_id=pmid,
        title=title,
        authors=authors,
        abstract=abstract,
        doi=doi,
        published_date=_best_date(article),
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        # Only PMC copies are freely fetchable; a bare PubMed record has no PDF.
        pdf_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/" if pmcid else "",
        source="pubmed",
        keywords=[_text(k) for k in article.findall(".//MedlineCitation/KeywordList/Keyword")],
        extra={
            "pmid": pmid,
            "pmcid": pmcid,
            # The date esearch actually filtered on. Differs from published_date
            # whenever PubMed indexed the paper later than it appeared.
            "entrez_date": (d.isoformat() if (d := _entrez_date(article)) else ""),
            "journal": _text(article.find(".//Journal/Title")),
            "publication_types": [
                _text(t) for t in article.findall(".//Article/PublicationTypeList/PublicationType")
            ],
        },
    )


def _esearch(term: str, since: date, until: date, max_results: int) -> tuple[list[str], int | None]:
    """Return matching PMIDs and the window's true match count.

    esearch orders newest-indexed first, so a ``max_results`` smaller than the
    match count does not sample the window — it returns only its final day or
    two. Reporting ``count`` is what makes that visible.
    """
    ids: list[str] = []
    available: int | None = None
    retstart = 0
    while len(ids) < max_results:
        payload = fetch_json(
            ESEARCH_URL,
            {
                "db": "pubmed",
                "term": term,
                "datetype": "edat",  # Entrez date: when PubMed got the record
                "mindate": f"{since:%Y/%m/%d}",
                "maxdate": f"{until:%Y/%m/%d}",
                "retmax": min(SEARCH_BATCH, max_results - len(ids)),
                "retstart": retstart,
                "retmode": "json",
                **_auth_params(),
            },
        )
        result = payload.get("esearchresult", {})
        if available is None:
            raw = result.get("count")
            available = int(raw) if str(raw).isdigit() else None
        batch = result.get("idlist", [])
        if not batch:
            break
        ids.extend(batch)
        retstart += len(batch)
        if len(batch) < SEARCH_BATCH:
            break
    return ids[:max_results], available


def search(
    *,
    since: date,
    until: date | None = None,
    keywords: list[str] | None = None,
    term: str | None = None,
    max_results: int = 200,
) -> SearchResult:
    """Fetch PubMed records whose Entrez date falls in ``[since, until]``.

    Pass either ``keywords`` (ORed together) or a raw PubMed ``term`` for full
    control over field tags and boolean structure. Generic method words are
    worth avoiding — ``"molecular dynamics"[Title/Abstract]`` matches most of
    physical chemistry, so pair them with a subject term via ``term``.

    Raises:
        ValueError: neither keywords nor term was supplied.
        FetchError: transport failure that outlived the retries.
    """
    until = until or date.today()
    if since > until:
        raise ValueError(f"since ({since}) is after until ({until})")

    if term is None:
        if not keywords:
            raise ValueError("pass keywords or a raw PubMed term")
        term = " OR ".join(f'"{k}"[Title/Abstract]' for k in keywords)

    ids, available = _esearch(term, since, until, max_results)
    if not ids:
        return SearchResult([], available, date_axis="entrez_date")

    papers: list[Paper] = []
    for start in range(0, len(ids), FETCH_BATCH):
        root = fetch_xml(
            EFETCH_URL,
            {
                "db": "pubmed",
                "id": ",".join(ids[start : start + FETCH_BATCH]),
                "retmode": "xml",
                **_auth_params(),
            },
        )
        # PubmedBookArticle is a sibling element, not a PubmedArticle — a
        # findall on the latter alone drops book chapters without a word.
        for article in root.findall(".//PubmedArticle") + root.findall(".//PubmedBookArticle"):
            if (paper := _parse_article(article)) is not None:
                papers.append(paper)

    papers.sort(key=lambda p: (p.published_date or date.min), reverse=True)

    # esearch's count is the number of *matches*; papers is what survived
    # fetching and parsing. Handing the raw count to SearchResult reports every
    # unparseable record as truncation, and tells the caller to raise a limit
    # that will change nothing. Reconcile here, and say what was lost.
    notes: list[str] = []
    swept_everything = available is None or len(ids) >= available
    dropped = len(ids) - len(papers)
    if dropped > 0:
        notes.append(
            f"{dropped} of {len(ids)} records fetched but not parsed "
            f"(no PMID, no title, or an unrecognised record type)"
        )
    return SearchResult(
        papers,
        len(papers) if swept_everything else available,
        date_axis="entrez_date",
        axis_range=_entrez_range(papers),
        notes=notes,
    )

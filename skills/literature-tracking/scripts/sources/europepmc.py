"""Europe PMC as a keyword-searchable window onto preprint servers.

bioRxiv and medRxiv have no keyword search of their own — you can filter by
subject area and nothing else, so a category sweep returns mostly papers
outside any specific interest. Europe PMC indexes those same preprints *and*
supports full text search with a date range, which fills exactly that gap.

It is a **complement, not a replacement**. Measured against the bioRxiv API on
2026-07-30:

======  ==========  ==========  ========
Date    Europe PMC  bioRxiv     Coverage
======  ==========  ==========  ========
07-23   183         261         70%
07-27   186         238         78%
07-28   199         299         67%
07-29   90          210         **43%**
07-30   0           —           **0%**
======  ==========  ==========  ========

Indexing lags roughly a day and even settles below 80%, so this cannot be the
only preprint channel — the newest days, which a tracking query cares about
most, are the worst covered. Run it alongside the direct fetch: records carry
the same DOI, so dedup merges them, and the merge itself becomes a relevance
signal (``extra["keyword_match"]``).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from models import Paper, SearchResult

from ._http import FetchError, SourceError, fetch_json

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE_SIZE = 100
MAX_PAGES = 50

#: Publisher names as Europe PMC spells them, keyed by our source names.
PUBLISHERS = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}

#: Characters that carry query-syntax meaning. Left in a keyword they do not
#: raise an error — Europe PMC answers a malformed query with HTTP 200 and
#: plausible-looking results for whatever it managed to parse.
_SYNTAX_CHARS = re.compile(r'["()\[\]{}:^~*?\\]')


class EuropePmcQueryError(SourceError):
    """Europe PMC did not run the query we sent."""


def _escape(term: str) -> str:
    """Strip query syntax out of a user keyword and quote it."""
    cleaned = " ".join(_SYNTAX_CHARS.sub(" ", term).split())
    return f'"{cleaned}"'


def build_query(
    keywords: list[str],
    since: date,
    until: date,
    publishers: list[str] | None = None,
) -> str:
    """Assemble a preprint-scoped Europe PMC query."""
    if not keywords:
        raise ValueError("Europe PMC is the keyword channel; it needs keywords")

    names = [PUBLISHERS[p] for p in (publishers or list(PUBLISHERS))]
    groups = [
        '(SRC:"PPR")',  # preprints only
        "(" + " OR ".join(f'PUBLISHER:"{n}"' for n in names) + ")",
        "(" + " OR ".join(_escape(k) for k in keywords) + ")",
        f"(FIRST_PDATE:[{since:%Y-%m-%d} TO {until:%Y-%m-%d}])",
    ]
    return " AND ".join(groups)


def _check_query_echo(payload: dict, sent: str) -> None:
    """Confirm Europe PMC ran what we sent.

    It silently drops clauses it cannot parse — an unbalanced bracket returns
    thousands of unrelated hits under HTTP 200 — but it echoes the query it
    actually used, so comparing the two catches the mangling.

    Fails **closed**. A missing or renamed ``request.queryString`` is what an
    upstream schema change looks like, and treating it as "nothing to compare,
    carry on" would disable the guard at exactly the moment it is needed —
    while every record still gets stamped ``keyword_match``, so the unrelated
    results arrive flagged as the ones to read first.
    """
    request = payload.get("request")
    echoed = request.get("queryString") if isinstance(request, dict) else None
    if not echoed:
        raise EuropePmcQueryError(
            "Europe PMC did not echo the query it ran (no request.queryString "
            "in the response), so we cannot tell whether it dropped a clause. "
            f"Sent: {sent}"
        )
    if " ".join(str(echoed).split()) != " ".join(sent.split()):
        raise EuropePmcQueryError(
            f"Europe PMC rewrote the query.\n  sent:   {sent}\n  ran:    {echoed}"
        )


#: Europe PMC returns titles and abstracts with inline markup left in:
#: ``peptidyl-prolyl <i>cis-trans</i> isomerization``. bioRxiv returns the same
#: title as plain text.
_MARKUP = re.compile(r"<[^>]+>")


def _strip_markup(text: str) -> str:
    """Remove inline HTML so titles compare equal across sources.

    Not cosmetic. ``dedup.title_fingerprint`` keeps only letters and digits, so
    an ``<i>`` survives as a literal ``i`` wedged into the middle of the
    fingerprint — and the title rule silently stops matching that record
    against its bioRxiv twin. Measured on a real pair: the two merged on DOI
    alone while every other duplicate in the run matched on both rules.

    Tags are deleted rather than replaced with a space — these are inline
    formatting spans, so ``<sup>13</sup>C`` has to come back as ``13C``, and
    the real spaces around a phrase like ``<i>cis-trans</i>`` are already there.
    """
    return " ".join(_MARKUP.sub("", text or "").split())


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _authors(record: dict) -> list[str]:
    listed = (record.get("authorList") or {}).get("author") or []
    names = [a.get("fullName", "").strip() for a in listed]
    if any(names):
        return [n for n in names if n]
    # authorString is "Smith J, Okafor A." — trailing period, comma-separated.
    return [a.strip() for a in record.get("authorString", "").rstrip(".").split(",") if a.strip()]


def _to_paper(record: dict) -> Paper:
    doi = (record.get("doi") or "").strip()
    publisher = (record.get("bookOrReportDetails") or {}).get("publisher", "")
    record_id = record.get("id", "")
    url = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/PPR/{record_id}"
    return Paper(
        paper_id=record_id,
        title=_strip_markup(record.get("title") or ""),
        authors=_authors(record),
        abstract=_strip_markup(record.get("abstractText") or ""),
        doi=doi,
        published_date=_parse_date(record.get("firstPublicationDate", "")),
        url=url,
        pdf_url="",
        source="europepmc",
        keywords=[],
        extra={
            # The whole point of this channel: reaching a record here means it
            # matched the query's keywords, which the direct fetch cannot tell us.
            "keyword_match": True,
            "preprint_server": publisher.lower(),
            "europepmc_id": record.get("id", ""),
        },
    )


def search(
    *,
    keywords: list[str],
    since: date,
    until: date | None = None,
    publishers: list[str] | None = None,
    max_results: int = 200,
) -> SearchResult:
    """Fetch preprints matching ``keywords`` in ``[since, until]``.

    Raises:
        EuropePmcQueryError: the query came back rewritten.
        ValueError: no keywords were supplied.
        FetchError: transport failure that outlived the retries.
    """
    until = until or date.today()
    if since > until:
        raise ValueError(f"since ({since}) is after until ({until})")

    query = build_query(keywords, since, until, publishers)
    papers: list[Paper] = []
    available: int | None = None
    cursor = "*"
    for _ in range(MAX_PAGES):
        if len(papers) >= max_results:
            break
        payload = fetch_json(
            BASE_URL,
            {
                "query": query,
                "format": "json",
                "resultType": "core",  # includes abstracts
                "pageSize": min(PAGE_SIZE, max_results - len(papers)),
                "cursorMark": cursor,
                "sort": "P_PDATE_D desc",  # newest first
            },
        )
        _check_query_echo(payload, query)
        if available is None and str(payload.get("hitCount")).isdigit():
            available = int(payload["hitCount"])

        batch = (payload.get("resultList") or {}).get("result") or []
        if not batch:
            break
        papers.extend(_to_paper(r) for r in batch)

        # A repeated cursor means the end; without this check we would loop.
        following = payload.get("nextCursorMark", "")
        if not following or following == cursor:
            break
        cursor = following

    return SearchResult(papers[:max_results], available)


__all__ = ["EuropePmcQueryError", "FetchError", "build_query", "search"]

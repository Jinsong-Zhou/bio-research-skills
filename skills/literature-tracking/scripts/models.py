"""Normalised paper record shared by every source adapter.

Field names deliberately mirror ``paper_search_mcp.paper.Paper`` (MIT,
https://github.com/openags/paper-search-mcp) so records from this skill stay
interchangeable with that ecosystem. Fields we never populate are omitted
rather than carried as dead weight; ``also_in`` / ``merge_reason`` are additions
needed by cross-source dedup.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass
class Paper:
    """One paper as seen from one source."""

    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    doi: str
    published_date: date | None
    url: str
    pdf_url: str
    source: str

    updated_date: date | None = None
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    citations: int = 0

    #: Records from other sources judged to be the same work, in the shape
    #: ``{"source": ..., "doi": ..., "url": ..., "paper_id": ...}``.
    also_in: list[dict[str, str]] = field(default_factory=list)
    #: Which dedup rule merged ``also_in`` in. Empty when nothing was merged.
    merge_reason: str = ""
    #: Source-specific leftovers (bioRxiv ``published``, PubMed ``pmid``, ...).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def year(self) -> int | None:
        return self.published_date.year if self.published_date else None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["published_date"] = self.published_date.isoformat() if self.published_date else None
        out["updated_date"] = self.updated_date.isoformat() if self.updated_date else None
        return out


@dataclass
class SearchResult:
    """What one source returned, and what it *could* have returned.

    ``available`` is the whole point. Every one of these APIs answers a
    too-large query by silently returning its newest slice, so a caller that
    only sees ``len(papers)`` cannot tell a complete sweep from one covering
    the last day of an eight-day window. Reporting the true count turns that
    into a number someone can act on.
    """

    papers: list[Paper]
    #: Records matching the query in the window, per the API's own count.
    #: ``None`` when the API does not report one.
    available: int | None = None

    def __len__(self) -> int:
        return len(self.papers)

    @property
    def truncated(self) -> bool:
        return self.available is not None and self.available > len(self.papers)

    @property
    def covered_range(self) -> tuple[date, date] | None:
        """Earliest and latest date actually returned, for spotting skew."""
        dates = [p.published_date for p in self.papers if p.published_date]
        return (min(dates), max(dates)) if dates else None


def dumps(papers: list[Paper], *, indent: int | None = 2) -> str:
    """Serialise papers to JSON."""
    return json.dumps([p.to_dict() for p in papers], indent=indent, ensure_ascii=False)

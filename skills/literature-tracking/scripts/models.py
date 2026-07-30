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


def dumps(papers: list[Paper], *, indent: int | None = 2) -> str:
    """Serialise papers to JSON."""
    return json.dumps([p.to_dict() for p in papers], indent=indent, ensure_ascii=False)

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
    #: ``{"source", "doi", "url", "paper_id", "published_date", "title"}``.
    #: ``title`` is carried so a wrong merge is visible in the output rather
    #: than hidden behind whichever record won the primary slot.
    also_in: list[dict[str, str]] = field(default_factory=list)
    #: Which dedup rules merged ``also_in`` in, joined by ``+`` when more than
    #: one agreed, e.g. ``"exact-doi+title-fingerprint"``. Empty when nothing
    #: was merged. Match on membership, never equality:
    #: ``"exact-doi" in paper.merge_reason.split("+")``.
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
    #: Records matching the query in the window, counted the same way
    #: ``papers`` is. ``None`` when the API does not report a usable count —
    #: which is *not* the same as zero, and must not be collapsed into it.
    #: A source that post-processes its rows (bioRxiv collapses versions) has
    #: to reconcile the API's count with what survived, or a complete sweep
    #: reports itself truncated.
    available: int | None = None
    #: Which date field the *search* was bounded on. PubMed searches on Entrez
    #: date and reports publication dates, so its records legitimately fall
    #: outside the requested window and ``covered_range`` must not be read as
    #: "the part of the window we reached".
    date_axis: str = "published_date"
    #: Range of ``date_axis`` across the returned records, when that field is
    #: not ``published_date``. Sources bounded on their own axis set this.
    axis_range: tuple[date, date] | None = None
    #: Anything the adapter had to do quietly — records it could not parse,
    #: fields it fell back on. Without these a dropped record is reported as
    #: truncation, and the caller is told to raise a limit that will not help.
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.papers)

    @property
    def coverage(self) -> str:
        """``complete`` | ``truncated`` | ``unknown``.

        Three states, because "the API did not tell us how much exists" is a
        real answer and reporting it as ``complete`` is how a half-swept
        window gets written up as a finished one.
        """
        if self.available is None:
            return "unknown"
        return "truncated" if self.available > len(self.papers) else "complete"

    @property
    def truncated(self) -> bool:
        """True only when we *know* records were left behind.

        Unknown coverage is deliberately not truthy here — check ``coverage``
        when the distinction matters.
        """
        return self.coverage == "truncated"

    @property
    def covered_range(self) -> tuple[date, date] | None:
        """Earliest and latest ``date_axis`` value returned, for spotting skew.

        Falls back to publication dates, which is the search axis for every
        source except PubMed.
        """
        if self.axis_range:
            return self.axis_range
        dates = [p.published_date for p in self.papers if p.published_date]
        return (min(dates), max(dates)) if dates else None


def dumps(papers: list[Paper], *, indent: int | None = 2) -> str:
    """Serialise papers to JSON."""
    return json.dumps([p.to_dict() for p in papers], indent=indent, ensure_ascii=False)

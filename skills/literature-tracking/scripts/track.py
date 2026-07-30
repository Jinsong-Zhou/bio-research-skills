#!/usr/bin/env python3
"""Fetch, normalise and deduplicate new papers across arXiv, bioRxiv/medRxiv and PubMed.

Deterministic by design: this script never calls an LLM and needs no API key.
It answers "what exists in this window, deduplicated" and nothing more —
relevance judgement belongs to the agent reading the output (see SKILL.md).

    python3 track.py --since 7d --keywords "cryo-EM" "protein structure" \\
        --biorxiv-categories biochemistry biophysics

Writes a JSON report to stdout; progress and warnings go to stderr, so
``python3 track.py ... > papers.json`` stays clean.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from typing import Any

from dedup import deduplicate
from models import Paper
from sources import arxiv, biorxiv, europepmc, pubmed
from sources._http import FetchError

SOURCE_CHOICES = ("arxiv", "biorxiv", "medrxiv", "pubmed", "europepmc")

#: Below this window width the Crossref dedup rule cannot pay off, so `auto`
#: turns it off. A preprint and the journal article it becomes are typically
#: months or years apart, and a rule that merges them only fires when *both*
#: land in the same query. Measured on a 7-day window: 60 lookups, 0 merges,
#: and neither side carried a usable relation — publishers rarely deposit
#: `has-preprint`, and a preprint posted this week has nothing to link to yet.
CROSSREF_MIN_WINDOW_DAYS = 60
_RELATIVE = re.compile(r"^(\d+)\s*([dwmy])$", re.IGNORECASE)
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def parse_since(value: str, *, today: date | None = None) -> date:
    """Accept ``7d`` / ``2w`` / ``3m`` / ``1y`` or an ISO date."""
    today = today or date.today()
    if match := _RELATIVE.match(value.strip()):
        count, unit = int(match.group(1)), match.group(2).lower()
        return today - timedelta(days=count * _UNIT_DAYS[unit])
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a relative window like '7d' or an ISO date, got {value!r}"
        ) from exc


def _collect(
    args: argparse.Namespace,
) -> tuple[list[Paper], list[dict[str, str]], dict[str, int]]:
    """Query every requested source, tolerating individual failures.

    A source that fails is recorded and skipped — a broken PubMed key should
    not cost you the arXiv results — but the failure is always reported, never
    swallowed into a silently short digest.
    """
    papers: list[Paper] = []
    errors: list[dict[str, str]] = []
    counts: dict[str, int] = {}

    def run(name: str, fetch) -> None:
        try:
            found = fetch()
        except (
            FetchError,
            ValueError,
            arxiv.ArxivQueryError,
            europepmc.EuropePmcQueryError,
        ) as exc:
            errors.append({"source": name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  {name}: FAILED — {exc}", file=sys.stderr)
            return
        counts[name] = len(found)
        papers.extend(found)
        print(f"  {name}: {len(found)} papers", file=sys.stderr)

    print(f"Window {args.since} .. {args.until}", file=sys.stderr)

    if "arxiv" in args.sources:
        run(
            "arxiv",
            lambda: arxiv.search(
                keywords=args.keywords,
                categories=args.arxiv_categories or list(arxiv.QBIO_CATEGORIES),
                since=args.since,
                until=args.until,
                max_results=args.max_per_source,
            ),
        )

    for server in ("biorxiv", "medrxiv"):
        if server not in args.sources:
            continue
        categories = args.biorxiv_categories if server == "biorxiv" else args.medrxiv_categories
        run(
            server,
            lambda server=server, categories=categories: biorxiv.search(
                since=args.since,
                until=args.until,
                categories=categories,
                server=server,
                max_results=args.max_per_source,
            ),
        )

    if "pubmed" in args.sources:
        run(
            "pubmed",
            lambda: pubmed.search(
                since=args.since,
                until=args.until,
                keywords=args.keywords,
                term=args.pubmed_term,
                max_results=args.max_per_source,
            ),
        )

    # The keyword channel onto the preprint servers. Needs keywords by
    # definition, and only makes sense for servers we are already tracking.
    servers = [s for s in ("biorxiv", "medrxiv") if s in args.sources]
    if "europepmc" in args.sources and args.keywords and servers:
        run(
            "europepmc",
            lambda: europepmc.search(
                keywords=args.keywords,
                since=args.since,
                until=args.until,
                publishers=servers,
                max_results=args.max_per_source,
            ),
        )

    return papers, errors, counts


def resolve_crossref(args: argparse.Namespace) -> tuple[bool, str]:
    """Decide whether to run the Crossref rule; return the choice and why not.

    ``auto`` weighs it against the window: the rule merges a preprint with the
    journal article it became, which only helps when both fall inside the same
    query. Over a week they almost never do.
    """
    if args.crossref == "on":
        return True, ""
    if args.crossref == "off":
        return False, "disabled with --crossref off"
    span = (args.until - args.since).days
    if span < CROSSREF_MIN_WINDOW_DAYS:
        return False, (
            f"{span}-day window is under {CROSSREF_MIN_WINDOW_DAYS} days, so a preprint "
            f"and its journal version cannot both be in range. Use --crossref on to force it"
        )
    return True, ""


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    papers, errors, counts = _collect(args)

    use_crossref, reason = resolve_crossref(args)
    if reason:
        print(f"  Crossref rule off: {reason}", file=sys.stderr)

    # Each Crossref request takes over a second, so this phase can run for
    # minutes with nothing to show. Say so, or it reads as a hang.
    budget = min(args.max_crossref_lookups, len(papers)) if use_crossref else 0
    detail = f"up to {budget} Crossref lookups, roughly {budget}s" if budget else "offline"
    print(f"Fetched {len(papers)} records; deduplicating ({detail})…", file=sys.stderr)

    merged, stats = deduplicate(
        papers,
        use_crossref=use_crossref,
        max_crossref_lookups=args.max_crossref_lookups,
    )

    print(
        f"  {stats.duplicates_removed} duplicates merged "
        f"({stats.merges_by_tier or 'none'}) → {stats.papers_out} unique",
        file=sys.stderr,
    )
    flagged = sum(1 for p in merged if p.extra.get("keyword_match"))
    if flagged:
        print(f"  {flagged} matched the keyword channel — read those first", file=sys.stderr)
    if stats.crossref_lookups:
        # Surface the yield so the budget can be tuned on evidence.
        print(
            f"  Crossref: {stats.crossref_lookups} lookups → "
            f"{stats.merges_by_tier.get('crossref-relation', 0)} merges",
            file=sys.stderr,
        )
    if stats.crossref_skipped:
        print(
            f"  WARNING: {stats.crossref_skipped} records skipped tier-2 lookup "
            f"(--max-crossref-lookups reached); some preprint/journal pairs may "
            f"still appear twice",
            file=sys.stderr,
        )

    return {
        "query": {
            "since": args.since.isoformat(),
            "until": args.until.isoformat(),
            "sources": list(args.sources),
            "keywords": args.keywords or [],
            "arxiv_categories": args.arxiv_categories or list(arxiv.QBIO_CATEGORIES),
            "biorxiv_categories": args.biorxiv_categories or [],
            "medrxiv_categories": args.medrxiv_categories or [],
            "pubmed_term": args.pubmed_term or "",
        },
        "stats": {
            "fetched_by_source": counts,
            "fetched_total": stats.papers_in,
            "unique_total": stats.papers_out,
            "keyword_matched": flagged,
            "duplicates_merged": stats.duplicates_removed,
            "merges_by_tier": stats.merges_by_tier,
            "crossref_lookups": stats.crossref_lookups,
            "crossref_failures": stats.crossref_failures,
            "crossref_skipped": stats.crossref_skipped,
        },
        # Present even when empty, so a caller can tell "nothing new" apart from
        # "three of four sources fell over".
        "errors": errors,
        "papers": [p.to_dict() for p in merged],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="track.py",
        description="Deduplicated multi-source literature tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--since", type=parse_since, default="7d",
        help="window start: relative (7d, 2w, 3m, 1y) or ISO date (default: 7d)",
    )
    parser.add_argument(
        "--until", type=parse_since, default=None,
        help="window end as an ISO date (default: today)",
    )
    parser.add_argument(
        "--sources", nargs="+", choices=SOURCE_CHOICES,
        default=["arxiv", "biorxiv", "pubmed", "europepmc"],
        help="which sources to query (default: all but medrxiv)",
    )
    parser.add_argument(
        "--keywords", nargs="+", default=None,
        help="terms ORed together for arXiv, PubMed and Europe PMC. The bioRxiv "
             "and medRxiv APIs ignore them — no keyword search exists there, "
             "which is what the europepmc channel is for; use categories",
    )
    parser.add_argument(
        "--arxiv-categories", nargs="+", default=None,
        help=f"arXiv categories (default: all of q-bio, {len(arxiv.QBIO_CATEGORIES)} of them)",
    )
    parser.add_argument(
        "--biorxiv-categories", nargs="+", default=None,
        help="bioRxiv subject areas, e.g. biochemistry biophysics (default: all)",
    )
    parser.add_argument(
        "--medrxiv-categories", nargs="+", default=None,
        help="medRxiv subject areas (default: all)",
    )
    parser.add_argument(
        "--pubmed-term", default=None,
        help="raw PubMed query, overriding --keywords for that source",
    )
    parser.add_argument(
        "--max-per-source", type=int, default=200,
        help="cap on records fetched per source (default: 200)",
    )
    parser.add_argument(
        "--crossref", choices=("auto", "on", "off"), default="auto",
        help=f"the Crossref dedup rule, which catches papers retitled between "
             f"preprint and publication. 'auto' (default) runs it only for "
             f"windows of {CROSSREF_MIN_WINDOW_DAYS}+ days — over a week a "
             f"preprint and its journal version are almost never both in range, "
             f"and each lookup costs over a second",
    )
    parser.add_argument(
        "--max-crossref-lookups", type=int, default=60,
        help="ceiling on Crossref requests, one per unmatched DOI (default: 60). "
             "Raise it when the run warns about skipped lookups",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.until = args.until or date.today()
    if args.since > args.until:
        print(f"error: --since ({args.since}) is after --until ({args.until})", file=sys.stderr)
        return 2

    report = build_report(args)
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    # Every source failing is a failure, even though the JSON is well-formed.
    return 1 if report["errors"] and not report["papers"] else 0


if __name__ == "__main__":
    sys.exit(main())

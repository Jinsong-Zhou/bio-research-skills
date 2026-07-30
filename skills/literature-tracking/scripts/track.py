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
from sources import arxiv, biorxiv, pubmed
from sources._http import FetchError

SOURCE_CHOICES = ("arxiv", "biorxiv", "medrxiv", "pubmed")
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
        except (FetchError, ValueError, arxiv.ArxivQueryError) as exc:
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

    return papers, errors, counts


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    papers, errors, counts = _collect(args)

    # Tier 2 issues one Crossref request per unmatched DOI, so this phase can
    # run for minutes with nothing to show. Say so, or it reads as a hang.
    budget = 0 if args.no_crossref else min(args.max_crossref_lookups, len(papers))
    detail = f"up to {budget} Crossref lookups, roughly {budget}s" if budget else "offline"
    print(f"Fetched {len(papers)} records; deduplicating ({detail})…", file=sys.stderr)

    merged, stats = deduplicate(
        papers,
        use_crossref=not args.no_crossref,
        max_crossref_lookups=args.max_crossref_lookups,
    )

    print(
        f"  {stats.duplicates_removed} duplicates merged "
        f"({stats.merges_by_tier or 'none'}) → {stats.papers_out} unique",
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
        "--sources", nargs="+", choices=SOURCE_CHOICES, default=["arxiv", "biorxiv", "pubmed"],
        help="which sources to query (default: arxiv biorxiv pubmed)",
    )
    parser.add_argument(
        "--keywords", nargs="+", default=None,
        help="terms ORed together for arXiv and PubMed. bioRxiv/medRxiv ignore "
             "these — their API has no keyword search; use categories instead",
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
        "--no-crossref", action="store_true",
        help="skip dedup tier 2; faster and fully offline, but misses "
             "preprint/journal pairs that bioRxiv has not yet linked",
    )
    parser.add_argument(
        "--max-crossref-lookups", type=int, default=60,
        help="ceiling on dedup tier-2 requests, one per unmatched DOI "
             "(default: 60). Raise it when the run warns about skipped lookups",
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

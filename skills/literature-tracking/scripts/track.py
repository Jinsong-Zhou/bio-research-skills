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
from models import Paper, SearchResult
from sources import arxiv, biorxiv, europepmc, pubmed
from sources._http import SourceError

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
    """Accept ``7d`` / ``2w`` / ``3m`` / ``1y`` or an ISO date.

    The window is inclusive at both ends, so ``7d`` spans exactly seven days
    ending today — ``today - 6``, not ``today - 7``.
    """
    today = today or date.today()
    if match := _RELATIVE.match(value.strip()):
        count, unit = int(match.group(1)), match.group(2).lower()
        return today - timedelta(days=count * _UNIT_DAYS[unit] - 1)
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a relative window like '7d' or an ISO date, got {value!r}"
        ) from exc


def _collect(
    args: argparse.Namespace,
) -> tuple[list[Paper], list[dict[str, str]], dict[str, dict[str, Any]]]:
    """Query every requested source, tolerating individual failures.

    A source that fails is recorded and skipped — a broken PubMed key should
    not cost you the arXiv results — but the failure is always reported, never
    swallowed into a silently short digest.

    Every requested source gets a ``coverage`` entry with a ``status``, whether
    it ran, failed or was skipped. A source that simply vanishes from the report
    is indistinguishable from one that returned nothing, and "nothing new this
    week" is exactly the wrong conclusion to reach by accident.
    """
    papers: list[Paper] = []
    errors: list[dict[str, str]] = []
    coverage: dict[str, dict[str, Any]] = {}

    def record(name: str, status: str, reason: str | None, result: SearchResult | None) -> None:
        """One coverage row per requested source, however it turned out."""
        # `is not None`, never a truthiness test: SearchResult defines __len__,
        # so an empty-but-complete sweep is falsy and would be filed as
        # "coverage unknown" — a source that found nothing, reported as a
        # source we could not measure.
        if result is None:
            coverage[name] = {
                "status": status,  # failed | skipped
                "reason": reason,
                "fetched": 0,
                "available": None,
                "coverage": "unknown",
                "truncated": False,
                "covers": None,
                "covers_field": None,
                "notes": [],
            }
            return

        window = result.covered_range
        coverage[name] = {
            "status": status,
            "reason": reason,
            "fetched": len(result),
            "available": result.available,
            # complete | truncated | unknown. `truncated` alone cannot express
            # "the API never told us how much exists", and defaulting that to
            # false is how a half-swept window gets written up as a finished one.
            "coverage": result.coverage,
            "truncated": result.truncated,
            "covers": [d.isoformat() for d in window] if window else None,
            # Which date field `covers` is measured on. PubMed searches on
            # Entrez date, so its span is not comparable to the others'.
            "covers_field": result.date_axis,
            "notes": result.notes,
        }

    def skip(name: str, reason: str) -> None:
        """Record a source we chose not to query, and why."""
        record(name, "skipped", reason, None)
        print(f"  {name}: SKIPPED — {reason}", file=sys.stderr)

    def run(name: str, fetch) -> None:
        try:
            result = fetch()
        except SourceError as exc:
            errors.append({"source": name, "error": f"{type(exc).__name__}: {exc}"})
            record(name, "failed", str(exc), None)
            print(f"  {name}: FAILED — {exc}", file=sys.stderr)
            return
        except Exception as exc:  # noqa: BLE001 — deliberate, see below
            # A KeyError or AttributeError here means an upstream field moved,
            # not that the source is down. Tolerating it keeps the other four
            # sources' results, which is the whole contract of this function —
            # but it is a defect, so it says so rather than reading like an
            # outage, and it names the type so the cause is reconstructable.
            errors.append(
                {
                    "source": name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "kind": "unexpected — a payload shape change or a bug in the adapter",
                }
            )
            record(name, "failed", f"{type(exc).__name__}: {exc}", None)
            print(f"  {name}: FAILED (unexpected {type(exc).__name__}) — {exc}", file=sys.stderr)
            return

        record(name, "ok", None, result)
        papers.extend(result.papers)

        line = f"  {name}: {len(result)} papers"
        if result.available is not None:
            line += f" of {result.available}"
        elif result.coverage == "unknown":
            line += " (source reported no total — coverage unknown)"
        print(line, file=sys.stderr)
        for note in result.notes:
            print(f"    NOTE: {note}", file=sys.stderr)

        if result.truncated and result.available is not None:
            # Every one of these APIs returns its newest slice first, so a
            # truncated fetch does not sample the window — it drops the early
            # days wholesale. Say which days actually survived, and on which
            # date field, or a PubMed span reads as wider than the window.
            covered = result.covered_range
            span = (
                f" — only covers {covered[0]} .. {covered[1]} by {result.date_axis}"
                if covered
                else ""
            )
            print(
                f"    TRUNCATED: {result.available - len(result)} more exist{span}. "
                f"Raise --max-per-source or narrow the query",
                file=sys.stderr,
            )

    print(f"Window {args.since} .. {args.until}", file=sys.stderr)

    if "arxiv" in args.sources:
        run(
            "arxiv",
            lambda: arxiv.search(
                # Deliberately NOT args.keywords: all of q-bio runs under a
                # hundred submissions a week, and ANDing keywords onto that cut
                # a measured window from 79 papers to 1. Opt in with
                # --arxiv-keywords when the category filter is too broad.
                keywords=args.arxiv_keywords,
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
    # Both preconditions are real, but neither may fail *quietly*: this channel
    # is the only relevance signal in the report, so an agent that sees
    # `keyword_matched: 0` with no explanation concludes "nothing matched your
    # interests" when in fact nothing was ever searched.
    if "europepmc" in args.sources:
        servers = [s for s in ("biorxiv", "medrxiv") if s in args.sources]
        if not args.keywords:
            skip(
                "europepmc",
                "no --keywords given, and this is the keyword channel — it has "
                "nothing to search for. Pass --keywords to enable it",
            )
        elif not servers:
            skip(
                "europepmc",
                "neither biorxiv nor medrxiv is in --sources; this channel only "
                "indexes those preprint servers. Add one to --sources",
            )
        else:
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

    return papers, errors, coverage


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
    span = (args.until - args.since).days + 1  # the window includes both ends
    if span < CROSSREF_MIN_WINDOW_DAYS:
        return False, (
            f"{span}-day window is under {CROSSREF_MIN_WINDOW_DAYS} days, so a preprint "
            f"and its journal version cannot both be in range. Use --crossref on to force it"
        )
    return True, ""


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    papers, errors, coverage = _collect(args)

    use_crossref, reason = resolve_crossref(args)
    if reason:
        print(f"  Crossref rule off: {reason}", file=sys.stderr)

    # Each Crossref request takes over a second, so this phase can run for
    # minutes with nothing to show. Say so, or it reads as a hang.
    budget = min(args.max_crossref_lookups, len(papers)) if use_crossref else 0
    detail = (
        f"up to {budget} Crossref lookups, roughly {round(budget * 1.4)}s" if budget else "offline"
    )
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
            f"  WARNING: {stats.crossref_skipped} records skipped the rule 4 lookup "
            f"(--max-crossref-lookups reached); some preprint/journal pairs may "
            f"still appear twice",
            file=sys.stderr,
        )
    if stats.unknown_sources:
        print(
            f"  WARNING: unknown source(s) {', '.join(stats.unknown_sources)} rank below "
            f"every known one and will lose the primary slot in any merge",
            file=sys.stderr,
        )

    return {
        "query": {
            "since": args.since.isoformat(),
            "until": args.until.isoformat(),
            "sources": list(args.sources),
            "keywords": args.keywords or [],
            "arxiv_categories": args.arxiv_categories or list(arxiv.QBIO_CATEGORIES),
            "arxiv_keywords": args.arxiv_keywords or [],
            "biorxiv_categories": args.biorxiv_categories or [],
            "medrxiv_categories": args.medrxiv_categories or [],
            "pubmed_term": args.pubmed_term or "",
        },
        "stats": {
            # Per source: how many came back, how many existed, whether the
            # fetch was cut short and which dates survived if so.
            "coverage_by_source": coverage,
            "fetched_by_source": {k: v["fetched"] for k, v in coverage.items()},
            "truncated_sources": [k for k, v in coverage.items() if v["truncated"]],
            # Ran, but could not say how much existed — so "not truncated" is
            # an absence of evidence, not a complete sweep. Skipped sources are
            # listed separately below rather than doubled up here.
            "unknown_coverage_sources": [
                k
                for k, v in coverage.items()
                if v["status"] == "ok" and v["coverage"] == "unknown"
            ],
            "skipped_sources": {
                k: v["reason"] for k, v in coverage.items() if v["status"] == "skipped"
            },
            "fetched_total": stats.papers_in,
            "unique_total": stats.papers_out,
            "keyword_matched": flagged,
            "duplicates_merged": stats.duplicates_removed,
            "merges_by_tier": stats.merges_by_tier,
            # Rules that agreed with a cheaper one still count here, so a rule
            # can be seen working even when it created no new merge.
            "rule_matches": stats.rule_matches,
            # Whether rule 4 ran at all. Without this, `crossref_lookups: 0`
            # is byte-identical to "we ran it and found nothing to look up" —
            # and with the default 7-day window `auto` always turns it off, so
            # that is the state most runs are actually in.
            "crossref": {
                "requested": args.crossref,
                "enabled": use_crossref,
                "reason": reason or None,
                "lookups": stats.crossref_lookups,
                "failures": stats.crossref_failures,
                "skipped": stats.crossref_skipped,
            },
            # Sources ranked below every known one; they lose merges silently.
            "unknown_sources": stats.unknown_sources,
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
        help="window end: an ISO date, or a relative form like 7d meaning that "
             "many days ago — the same grammar as --since (default: today)",
    )
    parser.add_argument(
        "--sources", nargs="+", choices=SOURCE_CHOICES,
        default=["arxiv", "biorxiv", "pubmed", "europepmc"],
        help="which sources to query (default: all but medrxiv)",
    )
    parser.add_argument(
        "--keywords", nargs="+", default=None,
        help="terms ORed together for PubMed and Europe PMC. NOT arXiv — see "
             "--arxiv-keywords. The bioRxiv and medRxiv APIs have no keyword "
             "search at all, which is what the europepmc channel is for; filter "
             "those two by category instead",
    )
    parser.add_argument(
        "--arxiv-categories", nargs="+", default=None,
        help=f"arXiv categories (default: all of q-bio, {len(arxiv.QBIO_CATEGORIES)} of them)",
    )
    parser.add_argument(
        "--arxiv-keywords", nargs="+", default=None,
        help="narrow arXiv with keywords too. Off by default and rarely worth "
             "it: q-bio runs under 100 submissions a week, and a measured "
             "window went from 79 papers to 1 once keywords were ANDed on",
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


def check_usage(args: argparse.Namespace) -> str | None:
    """Catch configuration mistakes before any network I/O. Return the message.

    These are caller errors, not source failures, so they stop the run instead
    of being tolerated the way an outage is. A mistyped subject area used to be
    swallowed into ``errors[]`` and the run exited 0 with that source silently
    absent — a plausible digest built on a query nobody actually made.
    """
    if args.since > args.until:
        return f"--since ({args.since}) is after --until ({args.until})"

    for server in ("biorxiv", "medrxiv"):
        categories = getattr(args, f"{server}_categories")
        if categories and server not in args.sources:
            return (
                f"--{server}-categories was given but {server!r} is not in --sources "
                f"({' '.join(args.sources)}), so those categories would be silently "
                f"ignored. Add '{server}' to --sources"
            )
        # Resolve up front: the API answers an unknown subject area with HTTP
        # 200 and every paper in the window, so this has to fail before we ask.
        for category in categories or []:
            try:
                biorxiv.resolve_category(category, server)
            except biorxiv.UnknownCategoryError as exc:
                return str(exc)
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.until = args.until or date.today()
    if (problem := check_usage(args)) is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    report = build_report(args)
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    # Exit 1 only when nothing worked. A quiet week with one flaky source is
    # not a failed run, and conflating the two is what makes "no new papers"
    # indistinguishable from "everything fell over" — the exact distinction
    # `errors` exists to preserve.
    ran = [c for c in report["stats"]["coverage_by_source"].values() if c["status"] != "skipped"]
    return 1 if ran and all(c["status"] == "failed" for c in ran) else 0


if __name__ == "__main__":
    sys.exit(main())

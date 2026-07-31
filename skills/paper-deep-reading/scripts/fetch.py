#!/usr/bin/env python3
"""Resolve a paper reference to a PDF on disk, or say plainly why there is none.

Accepts a local path, an arXiv id or URL, a DOI, a bioRxiv/medRxiv URL, a PMCID
or a PMID, and covers three routes to a full text: arXiv, bioRxiv/medRxiv, and
Europe PMC's open-access mirror of PubMed Central. Anything behind a paywall is
reported as such — with the abstract, when one is available — rather than
guessed at.

    python3 fetch.py 2501.01234 --out-dir /tmp/papers
    python3 fetch.py 10.1101/2024.01.15.575681 --out-dir /tmp/papers
    python3 fetch.py ~/Downloads/paper.pdf

Writes a JSON report to stdout; progress goes to stderr.

The one thing this script will not do is hand back a file that is not a PDF.
Every route here can answer with HTTP 200 and an HTML page, and a downloader
that trusts the status code saves the paywall notice as ``paper.pdf``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _http import FetchError, NotAPdfError, describe_non_pdf, download_pdf, fetch_json

BIORXIV_API = "https://api.biorxiv.org/details"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

#: bioRxiv and medRxiv share their DOI prefixes with each other, so the prefix
#: narrows the search to "a preprint" and the API decides which server.
#: ``10.64898`` is the newer one — both servers issued ``10.1101`` historically
#: and both issue ``10.64898`` now, so neither prefix maps to one server and
#: neither can be dropped. Measured 2026-07-30: a medRxiv window returned both.
PREPRINT_DOI_PREFIXES = ("10.1101/", "10.64898/")

#: Matched with ``fullmatch``, never ``search``. An unanchored search is what
#: turned ``10.3389/fnins.2013.00025`` — an ordinary Frontiers DOI — into the
#: arXiv id ``2013.00025``: the tail of that DOI has exactly the arXiv shape,
#: ``search`` discards the prefix that says otherwise, and the paper was then
#: reported as unavailable under a DOI this script had invented for it.
_ARXIV_NEW = re.compile(r"(?:arxiv[:/])?(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
#: The subject class may be lowercase and hyphenated (``cond-mat.stat-mech``),
#: not only the two uppercase letters of ``math.AG``.
_ARXIV_OLD = re.compile(r"(?:arxiv[:/])?([a-z-]+(?:\.[A-Za-z-]+)?/\d{7})(v\d+)?", re.IGNORECASE)
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?/?$", re.IGNORECASE)
#: arXiv's own DataCite DOI. Unwrapped rather than looked up, so an arXiv
#: paper referenced by DOI still takes the arXiv route.
_ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(.+)", re.IGNORECASE)
#: A versioned preprint landing path: the DOI, then ``v2``, then ``.full.pdf``.
_DOI_IN_PATH = re.compile(r"(10\.\d{4,9}/[^\s?#]+?)(?:v\d+)?(?:\.full|\.pdf)*/?$")
_DOI = re.compile(r"(10\.\d{4,9}/\S+)$")
_PMCID = re.compile(r"(PMC\d+)", re.IGNORECASE)
_PMID = re.compile(r"(?:pmid[:/])?(\d{6,9})", re.IGNORECASE)
#: A scheme, or a bare ``host.tld/…``. A DOI never matches: ``10.1101/`` has
#: digits where the top-level domain would be.
_URLISH = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/)", re.IGNORECASE)


class ResolutionError(RuntimeError):
    """The reference could not be turned into anything fetchable."""


def _bare_arxiv(value: str) -> str | None:
    """The arXiv id ``value`` *is*, or None if it merely ends with one."""
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.fullmatch(value)
        if match:
            # Strip the version suffix: v1 and v3 are the same paper, and the
            # bare id always resolves to the latest.
            return match.group(1)
    return None


def _doi_in(value: str) -> str | None:
    """The DOI ``value`` carries, if any.

    The versioned landing-path form is tried first: a preprint URL ends
    ``…575681v1.full.pdf``, and none of that belongs to the DOI.
    """
    versioned = _DOI_IN_PATH.search(value)
    if versioned and "/" in versioned.group(1):
        return versioned.group(1)
    bare = _DOI.search(value)
    return bare.group(1) if bare else None


def parse_identifier(raw: str) -> tuple[str, str]:
    """Classify ``raw`` as ``(kind, id)``.

    ``kind`` is one of ``local``, ``arxiv``, ``doi``, ``pmcid``, ``pmid``.

    Order is the whole design here. The self-identifying forms go first — a
    DOI always starts ``10.``, an arXiv URL says ``arxiv.org`` — and only then
    the forms recognised by shape alone. Testing the shapes first misroutes
    every reference whose tail happens to look like something else, and a
    misroute here is not a failed lookup: it produces a confident report about
    a paper the user did not ask for.
    """
    value = raw.strip()

    # A remote URL can also end in ``.pdf`` — check the scheme before the
    # suffix, or every ``…v2.full.pdf`` link is read as a file on disk.
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        path = Path(value).expanduser()
        if path.suffix.lower() == ".pdf" or path.exists():
            return "local", str(path.resolve())

    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)

    was_url = bool(_URLISH.match(value))
    if was_url:
        # The query string belongs to whatever produced the link, not to the
        # paper: a bioRxiv URL copied out of a feed reader carries ``?rss=1``,
        # and left on it becomes part of the DOI. The trailing slash is what
        # PubMed's own address bar and its Cite dialog both emit.
        value = re.split(r"[?#]", value, maxsplit=1)[0].rstrip("/")

    url_match = _ARXIV_URL.search(value)
    if url_match:
        value = url_match.group(1)

    arxiv_doi = _ARXIV_DOI.fullmatch(value)
    if arxiv_doi:
        value = arxiv_doi.group(1)

    doi = _doi_in(value)
    if doi:
        return "doi", doi

    arxiv_id = _bare_arxiv(value)
    if arxiv_id:
        return "arxiv", arxiv_id

    # A URL carries its PMCID or PMID in the last path segment; anything else
    # has to *be* one. Matching them anywhere in the string turned "Smith et
    # al. 2024 Nature 12345678" into a PMID lookup, and taking the last
    # segment unconditionally turned the mistyped arXiv id "cond-mat/070100"
    # into the PMID 070100 — a real, unrelated paper either way.
    tail = value.rsplit("/", 1)[-1] if was_url else value

    pmcid_match = _PMCID.fullmatch(tail)
    if pmcid_match:
        return "pmcid", pmcid_match.group(1).upper()

    pmid_match = _PMID.fullmatch(tail)
    if pmid_match:
        return "pmid", pmid_match.group(1)

    raise ResolutionError(
        f"cannot tell what {raw!r} is — pass a local .pdf path, an arXiv id, "
        "a DOI, a PMCID or a PMID"
    )


def resolve_arxiv(arxiv_id: str) -> dict[str, Any]:
    """arXiv PDFs are at a predictable URL; no lookup needed."""
    return {
        "kind": "arxiv",
        "id": arxiv_id,
        "doi": f"10.48550/arXiv.{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "landing_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def resolve_preprint(doi: str) -> dict[str, Any]:
    """Find which preprint server holds ``doi`` and what its latest version is.

    The DOI alone does not say whether a ``10.1101/`` record is on bioRxiv or
    medRxiv, and the PDF URL needs both the server and the version number.
    """
    for server in ("biorxiv", "medrxiv"):
        try:
            payload = fetch_json(f"{BIORXIV_API}/{server}/{doi}")
        except FetchError:
            continue

        collection = payload.get("collection") or []
        if not collection:
            continue

        # The API returns one entry per version, oldest first.
        latest = collection[-1]
        version = latest.get("version") or "1"
        return {
            "kind": server,
            "id": doi,
            "doi": doi,
            "version": version,
            "pdf_url": f"https://www.{server}.org/content/{doi}v{version}.full.pdf",
            "landing_url": f"https://www.{server}.org/content/{doi}v{version}",
            "metadata": {
                "title": latest.get("title", ""),
                "authors": _split_authors(latest.get("authors", "")),
                "date": latest.get("date", ""),
                "category": latest.get("category", ""),
            },
            # Set when the preprint has since appeared in a journal. Worth
            # surfacing: the version under review may differ from this one.
            # Unpublished records carry the literal string "NA", not an empty
            # field, so a plain truthiness check reports every preprint as
            # published (measured 2026-07-30).
            "published_as": _published_doi(latest.get("published")),
        }

    raise ResolutionError(f"{doi} is not on bioRxiv or medRxiv")


def _split_authors(raw: str) -> list[str]:
    return [a.strip() for a in raw.split(";") if a.strip()]


def _published_doi(raw: Any) -> str | None:
    """The journal DOI a preprint became, or None if it is still unpublished."""
    value = (raw or "").strip()
    return None if value.upper() in ("", "NA") else value


def _europepmc_query(kind: str, value: str) -> str:
    if kind == "doi":
        return f'DOI:"{value}"'
    if kind == "pmcid":
        return f"PMCID:{value}"
    return f"EXT_ID:{value} AND SRC:MED"


def resolve_europepmc(kind: str, value: str) -> dict[str, Any]:
    """Look ``value`` up in Europe PMC and find an open-access PDF if one exists.

    Europe PMC rather than NCBI's own PMC: it mirrors the same open-access
    subset, indexes preprints too, and does not gate automated requests the way
    ncbi.nlm.nih.gov does.
    """
    payload = fetch_json(
        EUROPEPMC_SEARCH,
        {
            "query": _europepmc_query(kind, value),
            "resultType": "core",
            "format": "json",
            "pageSize": 1,
        },
    )
    results = (payload.get("resultList") or {}).get("result") or []
    if not results:
        raise ResolutionError(f"Europe PMC has no record for {value}")

    record = results[0]
    pmcid = record.get("pmcid")
    resolved: dict[str, Any] = {
        "kind": "europepmc",
        "id": pmcid or record.get("id", value),
        "doi": record.get("doi"),
        "pdf_url": None,
        "landing_url": f"https://europepmc.org/article/{record.get('source', 'MED')}/"
        f"{record.get('id', '')}",
        "metadata": {
            "title": record.get("title", ""),
            "authors": _split_authors(record.get("authorString", "").replace(",", ";")),
            "date": record.get("firstPublicationDate", ""),
            "journal": (record.get("journalInfo") or {}).get("journal", {}).get("title", ""),
        },
        "abstract": record.get("abstractText"),
        "open_access": record.get("isOpenAccess") == "Y",
    }

    # Prefer a URL the API itself vouches for as open access; fall back to the
    # render endpoint, which works for anything in the EPMC full-text repo.
    for entry in (record.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if entry.get("documentStyle") == "pdf" and entry.get("availabilityCode") == "OA":
            resolved["pdf_url"] = entry.get("url")
            break
    if not resolved["pdf_url"] and pmcid and record.get("inEPMC") == "Y":
        resolved["pdf_url"] = f"https://europepmc.org/articles/{pmcid}?pdf=render"

    return resolved


def resolve(kind: str, value: str) -> dict[str, Any]:
    """Pick a route. The DOI prefix is a hint about which to try first, not a gate.

    Preprint prefixes are shared with journal publishers and change over time,
    so both orders end in the same fallback: whichever route was not tried.
    """
    if kind == "arxiv":
        return resolve_arxiv(value)

    if kind == "doi" and value.startswith(PREPRINT_DOI_PREFIXES):
        try:
            return resolve_preprint(value)
        except ResolutionError:
            # A preprint-prefixed DOI the servers disown is usually a journal
            # article from a publisher sharing the prefix.
            return resolve_europepmc("doi", value)

    try:
        return resolve_europepmc(kind, value)
    except ResolutionError as epmc_error:
        if kind != "doi":
            raise
        # Europe PMC indexes preprints but lags; a DOI it has never heard of
        # may still be a preprint posted under a prefix we do not know about.
        try:
            return resolve_preprint(value)
        except ResolutionError:
            # Report that both routes were tried. Letting the preprint error
            # surface alone reads as "this is not a preprint", when what
            # actually happened is that nothing has a record of it.
            raise ResolutionError(
                f"{value} is in neither Europe PMC nor bioRxiv/medRxiv ({epmc_error})"
            ) from epmc_error


def _safe_stem(resolved: dict[str, Any]) -> str:
    raw = str(resolved.get("id") or resolved.get("doi") or "paper")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or "paper"


def build_report(reference: str, out_dir: Path) -> dict[str, Any]:
    kind, value = parse_identifier(reference)
    warnings: list[str] = []

    if kind == "local":
        path = Path(value)
        if not path.exists():
            raise ResolutionError(f"{path} does not exist")
        with path.open("rb") as handle:
            head = handle.read(5)
        if head != b"%PDF-":
            warnings.append(
                f"{path.name} does not start with %PDF-; it may not be a readable PDF"
            )
        return {
            "fulltext": "full",
            "path": str(path),
            "bytes": path.stat().st_size,
            "resolved": {"kind": "local", "id": path.name},
            "metadata": {},
            "abstract": None,
            "warnings": warnings,
        }

    resolved = resolve(kind, value)
    metadata = resolved.pop("metadata", {})
    abstract = resolved.pop("abstract", None)
    published_as = resolved.pop("published_as", None)
    open_access = resolved.pop("open_access", None)

    if published_as:
        warnings.append(
            f"this preprint was later published as {published_as}; the journal "
            "version may differ from what you are about to read"
        )
    if open_access is False:
        warnings.append("Europe PMC reports this record as not open access")

    report: dict[str, Any] = {
        "fulltext": "abstract-only",
        "path": None,
        "bytes": 0,
        "resolved": resolved,
        "metadata": metadata,
        "abstract": abstract,
        "warnings": warnings,
    }

    if not resolved.get("pdf_url"):
        warnings.append("no open-access PDF is available for this record")
        return report

    dest = out_dir / f"{_safe_stem(resolved)}.pdf"
    print(f"downloading {resolved['pdf_url']}", file=sys.stderr)
    try:
        size = download_pdf(resolved["pdf_url"], dest)
    except NotAPdfError as exc:
        warnings.append(f"{describe_non_pdf(exc)} ({exc.url})")
        return report
    except FetchError as exc:
        warnings.append(f"download failed: {exc}")
        return report

    report["fulltext"] = "full"
    report["path"] = str(dest)
    report["bytes"] = size
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a paper reference to a PDF on disk.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "reference",
        help="local .pdf path, arXiv id or URL, DOI, bioRxiv/medRxiv URL, PMCID, or PMID",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="where to write the downloaded PDF (default: current directory)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(args.reference, args.out_dir)
    except (ResolutionError, FetchError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["fulltext"] == "abstract-only":
        print(
            "WARNING: no full text. Anything you write from the abstract alone "
            "is a summary, not a deep read.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

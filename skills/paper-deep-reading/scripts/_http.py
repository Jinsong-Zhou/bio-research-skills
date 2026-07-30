"""Stdlib-only HTTP: JSON, and binary downloads that verify what arrived.

Deliberately duplicated from ``literature-tracking``'s ``sources/_http.py``
rather than shared. Skills install one at a time (``npx skills add --skill
paper-deep-reading``), so a cross-skill import would break for anyone who did
not take both. The duplication is the price of that guarantee.

The download half is new here, and it is the part that matters: every source
below can answer a PDF request with an HTML page and HTTP 200 — a paywall
interstitial, a "preparing your download" shim, a captcha. Trusting the status
code writes those straight to disk as ``paper.pdf``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONTACT = os.environ.get("BIO_RESEARCH_CONTACT", "bio-research-skills@example.invalid")
USER_AGENT = f"bio-research-skills/0.1 (paper-deep-reading; mailto:{CONTACT})"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Minimum seconds between requests to the same host.
_HOST_INTERVAL: dict[str, float] = {
    "export.arxiv.org": 3.0,
    "arxiv.org": 1.0,
    "api.biorxiv.org": 0.5,
    "www.ebi.ac.uk": 0.2,
    "www.ncbi.nlm.nih.gov": 0.34,
}
_DEFAULT_INTERVAL = 0.5
_last_call: dict[str, float] = {}

#: A PDF starts with these bytes. Anything else claiming to be one is not.
PDF_MAGIC = b"%PDF-"

#: Below this, a "PDF" is a stub or an error page that happens to start right.
MIN_PDF_BYTES = 4096


class FetchError(RuntimeError):
    """Transport-level failure that survived every retry."""


class NotAPdfError(RuntimeError):
    """The server returned 200 and the body was not a PDF.

    Carries the first bytes so the caller can say *what* came back instead —
    a login page and a rate-limit notice need different advice.
    """

    def __init__(self, url: str, head: bytes, size: int) -> None:
        self.url = url
        self.head = head
        self.size = size
        super().__init__(f"not a PDF: {url} returned {size} bytes starting {head[:40]!r}")


def _pace(host: str) -> None:
    interval = _HOST_INTERVAL.get(host, _DEFAULT_INTERVAL)
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call[host] = time.monotonic()


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if raw:
        try:
            return min(float(raw), 60.0)
        except ValueError:
            pass
    return min(2.0**attempt, 30.0)


def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET ``url`` and return the raw body, retrying transient failures."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean, doseq=True)}"

    host = urllib.parse.urlparse(url).netloc
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}

    last_error: Exception | None = None
    for attempt in range(retries):
        _pace(host)
        req = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES:
                raise FetchError(f"HTTP {exc.code} from {url}") from exc
            if attempt < retries - 1:
                time.sleep(_retry_after(exc, attempt))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2.0**attempt, 30.0))

    raise FetchError(f"giving up on {url} after {retries} attempts: {last_error}")


def fetch_json(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    body = fetch(url, params, **kwargs)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"non-JSON response from {url}: {body[:200]!r}") from exc


def download_pdf(url: str, dest: Path, *, timeout: float = 120.0) -> int:
    """Download ``url`` to ``dest``, but only if it is really a PDF.

    Nothing is written unless both checks pass, so a failed attempt never
    leaves a plausible-looking file behind for the next step to "read".

    Raises:
        NotAPdfError: body is not a PDF, or is too small to be a paper.
        FetchError: transport failure.
    """
    body = fetch(url, timeout=timeout, headers={"Accept": "application/pdf"})
    if not body.startswith(PDF_MAGIC) or len(body) < MIN_PDF_BYTES:
        raise NotAPdfError(url, body[:200], len(body))

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return len(body)


def describe_non_pdf(exc: NotAPdfError) -> str:
    """Turn a rejected body into advice, not just a byte dump."""
    head = exc.head.lower()
    if b"<html" in head or b"<!doctype" in head:
        if b"captcha" in head or b"cloudflare" in head:
            return "the host served a bot check; fetch it in a browser and pass the local path"
        return "the host served an HTML page, usually a paywall or landing page, not the PDF"
    if exc.size < MIN_PDF_BYTES and exc.head.startswith(PDF_MAGIC):
        return f"a {exc.size}-byte PDF, too small to be a paper — probably a placeholder"
    return "the response was not a PDF"

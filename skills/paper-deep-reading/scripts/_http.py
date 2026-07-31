"""Stdlib-only HTTP: JSON, and binary downloads that verify what arrived.

Deliberately duplicated from ``literature-tracking``'s ``sources/_http.py``
rather than shared. Skills install one at a time (``npx skills add --skill
paper-deep-reading``), so a cross-skill import would break for anyone who did
not take both. The duplication is the price of that guarantee.

The download half is new here, and it is the part that matters: every source
below can answer a PDF request with an HTML page and HTTP 200 — a paywall
interstitial, a "preparing your download" shim, a captcha. Trusting the status
code writes those straight to disk as ``paper.pdf``.

Checking the first bytes is necessary and not sufficient. A connection cut
mid-transfer leaves a file that starts like a PDF, opens like a PDF, and is
missing its end — which for a paper is the discussion, the limitations and the
supplementary material. So ``fetch`` keeps the response metadata a bare
``read()`` throws away, and ``download_pdf`` checks both ends.
"""

from __future__ import annotations

import datetime
import email.utils
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

CONTACT = os.environ.get("BIO_RESEARCH_CONTACT", "bio-research-skills@example.invalid")
USER_AGENT = f"bio-research-skills/0.1 (paper-deep-reading; mailto:{CONTACT})"

#: 408 is here because api.biorxiv.org served one during review. It is a
#: timeout the server is reporting on itself, which is exactly the transient
#: case retrying is for — and without it the first attempt raised, and a
#: preprint that exists was reported as not being one.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Minimum seconds between requests to the same host, at the rate each one
#: publishes. Only the hosts this skill actually calls are listed — the copy
#: this came from also paced ``export.arxiv.org`` and ``ncbi.nlm.nih.gov``,
#: neither of which ``fetch.py`` has a route to, and an interval for a host
#: nobody contacts reads as documentation of a request that is never made.
#:
#: Pacing is per process, and ``fetch.py`` runs once per paper, so the first
#: request of every run is unpaced. That is within every published limit here
#: for a human reading one paper at a time; it would not be for a loop.
_HOST_INTERVAL: dict[str, float] = {
    "arxiv.org": 1.0,  # arXiv asks for one request per second
    "api.biorxiv.org": 0.5,
    "www.ebi.ac.uk": 0.2,  # Europe PMC allows far more; this is courtesy
}
_DEFAULT_INTERVAL = 0.5
_last_call: dict[str, float] = {}

#: A PDF starts with these bytes. Anything else claiming to be one is not.
PDF_MAGIC = b"%PDF-"

#: …and ends with these. Checking only the start cannot see truncation,
#: because what a cut connection removes is the end — and the end of a paper
#: is the discussion, the limitations and the supplementary material, which is
#: most of what an assessment is built from.
PDF_TRAILER = b"%%EOF"

#: How far back from the end to look for the trailer. Some producers leave
#: padding or a linearisation hint after it.
TRAILER_WINDOW = 4096

#: Below this, a "PDF" is a stub or an error page that happens to start right.
MIN_PDF_BYTES = 4096


class FetchError(RuntimeError):
    """Transport-level failure that survived every retry."""


class NotAPdfError(FetchError):
    """The server returned 200 and the body was not a PDF.

    Carries the first bytes so the caller can say *what* came back instead —
    a login page and a rate-limit notice need different advice.

    A ``FetchError`` subclass because ``download_pdf`` documents the two
    together as its raise set: an ``except FetchError`` that did not catch a
    paywall page saved as ``paper.pdf`` would be a guard with a hole in it.
    """

    def __init__(self, url: str, head: bytes, size: int) -> None:
        self.url = url
        self.head = head
        self.size = size
        super().__init__(f"not a PDF: {url} returned {size} bytes starting {head[:40]!r}")


class TruncatedPdfError(FetchError):
    """The body began as a PDF but did not finish.

    Distinct from ``NotAPdfError``: this really is the paper, and re-fetching
    it is likely to work. Reported rather than saved, because a PDF missing
    its last pages reads as complete.
    """

    def __init__(self, url: str, size: int, expected: int | None) -> None:
        self.url = url
        self.size = size
        self.expected = expected
        detail = f"{size} of {expected} bytes" if expected else f"{size} bytes, no end marker"
        super().__init__(f"truncated download: {url} returned {detail}")


class Response(NamedTuple):
    """A body plus the two things a plain ``read()`` throws away.

    ``url`` is the address after redirects, which is not always the one asked
    for; ``length`` is the ``Content-Length`` the server promised, which is
    the only way to tell a complete body from a cut one.
    """

    body: bytes
    url: str
    length: int | None


def _pace(host: str) -> None:
    interval = _HOST_INTERVAL.get(host, _DEFAULT_INTERVAL)
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call[host] = time.monotonic()


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """How long the server asked us to wait, or our own backoff.

    Both RFC 9110 forms are honoured. The clamp to zero is not theoretical:
    ``Retry-After: -1`` reached ``time.sleep(-1)``, which raises, so a
    misbehaving header turned a retryable response into a crash.
    """
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if raw:
        try:
            return min(max(float(raw), 0.0), 60.0)
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            return min(max((when - now).total_seconds(), 0.0), 60.0)
    return min(2.0**attempt, 30.0)


def fetch_response(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    headers: dict[str, str] | None = None,
) -> Response:
    """GET ``url``, retrying transient failures, keeping the response metadata."""
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
                raw_length = response.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else None
                except ValueError:
                    length = None
                return Response(response.read(), response.geturl(), length)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES:
                raise FetchError(f"HTTP {exc.code} from {url}") from exc
            if attempt < retries - 1:
                time.sleep(_retry_after(exc, attempt))
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            # IncompleteRead is an HTTPException, not an OSError, so a
            # connection cut mid-body used to escape every handler here and
            # surface as a traceback with no JSON report at all.
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2.0**attempt, 30.0))

    raise FetchError(f"giving up on {url} after {retries} attempts: {last_error}")


def fetch(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> bytes:
    """GET ``url`` and return the raw body, retrying transient failures."""
    return fetch_response(url, params, **kwargs).body


def fetch_json(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    body = fetch(url, params, **kwargs)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"non-JSON response from {url}: {body[:200]!r}") from exc


def download_pdf(url: str, dest: Path, *, timeout: float = 120.0) -> Response:
    """Download ``url`` to ``dest``, but only if it is really a whole PDF.

    Nothing is written unless every check passes, so a failed attempt never
    leaves a plausible-looking file behind for the next step to "read".

    The returned ``Response`` carries the address the bytes actually came
    from, which is not always the one asked for — a caller that records only
    the requested URL cannot show where a redirect ended up.

    Raises:
        NotAPdfError: body is not a PDF, or is too small to be a paper.
        TruncatedPdfError: body is a PDF that stops before its end marker.
        FetchError: transport failure.
    """
    response = fetch_response(url, timeout=timeout, headers={"Accept": "application/pdf"})
    body = response.body
    if not body.startswith(PDF_MAGIC) or len(body) < MIN_PDF_BYTES:
        raise NotAPdfError(url, body[:200], len(body))
    if response.length is not None and response.length != len(body):
        raise TruncatedPdfError(url, len(body), response.length)
    if PDF_TRAILER not in body[-TRAILER_WINDOW:]:
        raise TruncatedPdfError(url, len(body), None)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return response


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

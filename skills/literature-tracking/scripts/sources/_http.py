"""Stdlib-only HTTP with retry, backoff and per-host pacing.

Every literature API here rate-limits, and several of them signal failure with
HTTP 200 (see ``references/source-quirks.md``). This module handles the
transport half — status codes, retries, pacing. Detecting *semantic* failure
inside a 200 response is each adapter's job.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from xml.etree import ElementTree as ET

#: Contact address advertised to APIs that ask for one (NCBI, Crossref).
#: Being identifiable buys higher rate limits and a warning instead of a ban.
CONTACT = os.environ.get("BIO_RESEARCH_CONTACT", "bio-research-skills@example.invalid")
USER_AGENT = f"bio-research-skills/0.1 (literature-tracking; mailto:{CONTACT})"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Minimum seconds between requests to the same host.
_HOST_INTERVAL: dict[str, float] = {
    "eutils.ncbi.nlm.nih.gov": 0.34,  # 3 req/s without an API key
    "export.arxiv.org": 3.0,  # arXiv asks for one request per 3 seconds
    "api.biorxiv.org": 0.5,
    "api.crossref.org": 0.1,
}
_DEFAULT_INTERVAL = 0.5
_last_call: dict[str, float] = {}


class SourceError(RuntimeError):
    """A source could not be queried.

    Base for every "this source failed, carry on with the others" condition, so
    a caller can catch the category instead of enumerating its members. The
    enumeration is what rots: adding a sixth adapter with its own error type
    used to mean the orchestrator silently stopped tolerating it, and one
    source's failure took down a run that had already fetched four others.

    Deliberately *not* a base for caller errors like an unknown subject area —
    those are config mistakes and should stop the run, not be tolerated.
    """


class FetchError(SourceError):
    """Transport-level failure that survived every retry."""


def _pace(host: str) -> None:
    interval = _HOST_INTERVAL.get(host, _DEFAULT_INTERVAL)
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_call[host] = time.monotonic()


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honour a Retry-After header, else exponential backoff."""
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
    """GET ``url`` and return the raw body, retrying transient failures.

    Raises:
        FetchError: on a non-retryable status, or once retries are exhausted.
    """
    if params:
        # Drop None values so callers can pass optional params unconditionally.
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
            # Includes the intermittent TLS EOF that biorxiv.org throws under load.
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


def fetch_xml(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> ET.Element:
    body = fetch(url, params, **kwargs)
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise FetchError(f"malformed XML from {url}: {body[:200]!r}") from exc

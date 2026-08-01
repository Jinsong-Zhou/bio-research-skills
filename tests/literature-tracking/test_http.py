"""The transport layer, which had no tests at all.

Three of the defects a mutation sweep found lived in this file — 429 no longer
retried, ``Retry-After`` ignored, and ``None`` params no longer dropped — and
every one of them is invisible from the outside: the run just gets slower, or
quietly asks the wrong question.
"""

from __future__ import annotations

import urllib.error
from datetime import date

import pytest
from sources import biorxiv
from sources._http import FetchError, SourceError, fetch, fetch_json


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "boom", headers or {}, None)  # type: ignore[arg-type]


@pytest.fixture
def transport(monkeypatch):
    """Drive ``urlopen`` from a script of responses, recording what was sent."""

    class Transport:
        def __init__(self):
            self.urls: list[str] = []
            self.slept: list[float] = []
            self.script: list = []

        def install(self, *script):
            self.script = list(script)

            def urlopen(request, timeout=None):
                self.urls.append(request.full_url)
                item = self.script.pop(0) if self.script else _Response(b"{}")
                if isinstance(item, BaseException):
                    raise item
                return item

            monkeypatch.setattr("urllib.request.urlopen", urlopen)
            monkeypatch.setattr("sources._http.time.sleep", self.slept.append)
            # Per-host pacing also sleeps, and mixing it into the log would
            # hide which delay came from the retry policy. Not under test here.
            monkeypatch.setattr("sources._http._pace", lambda host: None)

    return Transport()


class TestParams:
    def test_none_values_are_dropped_from_the_query_string(self, transport):
        """``{"category": None}`` must not become ``category=None``.

        bioRxiv treats an unrecognised category as "no filter" and returns
        every paper in the window — real records, entirely unrelated — so the
        literal string "None" is a silent, plausible wrong answer.
        """
        transport.install(_Response(b"{}"))
        fetch("https://api.biorxiv.org/details", {"category": None, "format": "json"})
        (url,) = transport.urls
        assert "category" not in url
        assert "format=json" in url

    def test_supplied_values_survive(self, transport):
        transport.install(_Response(b"{}"))
        fetch("https://api.biorxiv.org/details", {"category": "cell_biology"})
        assert "category=cell_biology" in transport.urls[0]


class TestRetries:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_transient_statuses_are_retried_then_give_up(self, transport, code):
        transport.install(*[_http_error(code) for _ in range(3)])
        with pytest.raises(FetchError, match="after 3 attempts"):
            fetch("https://api.crossref.org/works/x", retries=3)
        assert len(transport.urls) == 3

    def test_a_permanent_status_fails_on_the_first_try(self, transport):
        """404 is an answer, not a hiccup — three of them is just slower."""
        transport.install(_http_error(404))
        with pytest.raises(FetchError, match="HTTP 404"):
            fetch("https://api.crossref.org/works/10.48550/arXiv.2601.01234")
        assert len(transport.urls) == 1

    def test_retry_after_is_honoured_over_exponential_backoff(self, transport):
        """Ignoring it is how a polite client earns a ban."""
        transport.install(_http_error(429, {"Retry-After": "7"}), _Response(b"{}"))
        fetch("https://eutils.ncbi.nlm.nih.gov/x", retries=3)
        assert transport.slept == [7.0], "backoff overrode the server's instruction"

    def test_an_absurd_retry_after_is_capped(self, transport):
        transport.install(_http_error(503, {"Retry-After": "9999"}), _Response(b"{}"))
        fetch("https://api.crossref.org/works/x")
        assert transport.slept == [60.0]

    def test_an_http_date_retry_after_falls_back_to_backoff(self, transport):
        """The header may be a date, which float() cannot parse."""
        transport.install(
            _http_error(503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            _Response(b"{}"),
        )
        fetch("https://api.crossref.org/works/x")
        assert transport.slept == [1.0], "an unparseable header must not crash the retry"

    def test_a_tls_eof_is_retried(self, transport):
        """biorxiv.org throws this intermittently under load."""
        transport.install(OSError("EOF occurred in violation of protocol"), _Response(b"{}"))
        assert fetch("https://api.biorxiv.org/details") == b"{}"


class TestDecoding:
    def test_html_where_json_was_promised_is_an_error_not_a_crash(self, transport):
        transport.install(_Response(b"<html>502 Bad Gateway</html>"))
        with pytest.raises(FetchError, match="non-JSON"):
            fetch_json("https://api.crossref.org/works/x")

    def test_every_transport_failure_is_catchable_as_one_category(self, transport):
        """Callers catch SourceError; FetchError must stay inside that tree.

        The orchestrator used to enumerate error types, so a new one silently
        stopped being tolerated and took the whole run down with it.
        """
        transport.install(_http_error(404))
        with pytest.raises(SourceError):
            fetch("https://api.crossref.org/works/x")


class TestAdapterErrorsShareTheBase:
    def test_query_errors_are_source_errors(self):
        from sources.arxiv import ArxivQueryError
        from sources.europepmc import EuropePmcQueryError

        assert issubclass(ArxivQueryError, SourceError)
        assert issubclass(EuropePmcQueryError, SourceError)

    def test_an_unknown_category_is_not_a_source_error(self):
        """It is a caller mistake, and must stop the run rather than be tolerated."""
        assert not issubclass(biorxiv.UnknownCategoryError, SourceError)
        with pytest.raises(biorxiv.UnknownCategoryError):
            biorxiv.search(since=date(2026, 7, 1), categories=["protein folding"])

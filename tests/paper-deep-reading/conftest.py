"""Shared test setup: import path, and a hard boundary around the network.

The scripts run directly (``python3 scripts/fetch.py``), which makes
``scripts/`` the import root at runtime. Tests reproduce that.

These live under ``tests/`` rather than inside the skill because
``npx skills add`` copies a skill directory wholesale — it skips the directories
``.git``, ``__pycache__`` and ``__pypackages__``, and the file ``metadata.json``,
and nothing else. Colocated tests therefore shipped to every customer, who
neither runs them nor wants them in the tree their agent reads.
"""

import socket
import sys
import urllib.request
from pathlib import Path

import pytest

# This directory is named after the skill it covers, so the mapping cannot drift
# silently: rename one without the other and the assertion below says so.
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / HERE.name / "scripts"
assert SCRIPTS.is_dir(), f"no skills/{HERE.name}/scripts to test against"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class _NetworkUsedInOfflineTest(BaseException):
    """Deliberately not an ``Exception``.

    ``build_report`` folds a ``FetchError`` into ``report["warnings"]`` on
    purpose — a download that fails should degrade to abstract-only rather
    than abort. That same tolerance would swallow this guard and turn a
    network-using test back into a passing one, which is the bug it exists to
    catch. Sitting outside ``Exception`` keeps it unswallowable.
    """


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Fail any non-``live`` test that reaches for the network.

    ``test_both_preprint_prefixes_go_to_the_preprint_servers`` stubbed
    ``resolve_preprint`` but not ``resolve_europepmc``, and stayed offline
    only because the happy path never reached the fallback. Perturb the
    routing and that "offline" test made a real request — 13 seconds on a
    connected machine, and the full retry budget on a CI box without egress.
    """
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*args, **kwargs):
        raise _NetworkUsedInOfflineTest(
            "this test tried to open a network connection. Stub _http.fetch_response "
            "or the resolve_* function under test, or mark the test with "
            "@pytest.mark.live if hitting the real API is the point."
        )

    # Both, not just `urlopen`: patching the convenience wrapper alone leaves
    # `http.client`, `ssl` and a raw socket free to reach the network, so the
    # guard would cover the way these tests happen to be written today rather
    # than the boundary it claims to hold.
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

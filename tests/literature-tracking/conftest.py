"""Shared test setup: import path, and a hard boundary around the network.

The scripts run directly (``python3 scripts/track.py``), which makes
``scripts/`` the import root at runtime. Tests reproduce that.

These live under ``tests/`` rather than inside the skill because
``npx skills add`` copies a skill directory wholesale — its only exclusions are
``.git``, ``__pycache__`` and ``__pypackages__``. Colocated tests therefore
shipped to every customer, who neither runs them nor wants them in the tree
their agent reads.
"""

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

    ``track._collect`` catches ``Exception`` per source on purpose, so that one
    adapter blowing up on a renamed upstream field does not discard the four
    sources that already succeeded. That same breadth would swallow this guard
    and turn a network-using test back into a passing one — which is precisely
    the bug being fixed. Sitting outside ``Exception`` keeps it unswallowable.
    """


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Fail any non-``live`` test that reaches for the network.

    Two tests in this suite were documented as "fully offline" while quietly
    making nine requests to ebi.ac.uk, and they passed either way — the
    orchestrator folds a ``FetchError`` into ``report["errors"]`` and neither
    test asserted that list was empty. So they were slow, flaky against an
    outage, and covering none of the code they appeared to cover.

    A stub that raises is the only way to keep that from recurring: an offline
    test cannot accidentally depend on a live API if reaching one is an error.
    """
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*args, **kwargs):
        raise _NetworkUsedInOfflineTest(
            "this test tried to open a network connection. Stub the source's "
            "search()/fetch, or mark the test with @pytest.mark.live if hitting "
            "the real API is the point."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)

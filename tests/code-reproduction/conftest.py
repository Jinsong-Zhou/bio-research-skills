"""Shared test setup: import path, a network boundary, and a repo builder.

The scripts run directly (``python3 scripts/survey.py``), which makes
``scripts/`` the import root at runtime. Tests reproduce that.

These live under ``tests/`` rather than inside the skill because
``npx skills add`` copies a skill directory wholesale — its only exclusions are
``.git``, ``__pycache__`` and ``__pypackages__``. Colocated tests therefore
shipped to every customer, who neither runs them nor wants them in the tree
their agent reads.
"""

from __future__ import annotations

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

    ``probe_reachability`` catches ``OSError`` and folds it into
    ``{"reachable": false}`` — which is right, an unreachable host is a
    finding rather than a crash. That same tolerance would swallow this guard
    and turn a network-using test into a passing one that quietly reports
    every host as unreachable. Sitting outside ``Exception`` keeps it
    unswallowable.
    """


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Fail any non-``live`` test that opens a socket."""
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*args, **kwargs):
        raise _NetworkUsedInOfflineTest(
            "this test tried to open a network connection. Stub the probe function "
            "under test, or mark the test @pytest.mark.live if reaching the real "
            "host is the point."
        )

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture
def make_repo(tmp_path):
    """Build a throwaway checkout from a {path: contents} mapping."""

    def build(files: dict[str, str], name: str = "repo") -> Path:
        root = tmp_path / name
        for relative, contents in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        root.mkdir(parents=True, exist_ok=True)
        return root

    return build

"""Live checks that the hazards this skill documents are still real.

`references/repro-hazards.md` cites Proteina-Complexa by file and line. Those
citations rot: a repository fixes its build script, relicenses its weights,
adds CI. When that happens the documentation here becomes wrong, and a reader
who checks the citation finds nothing — which is worse than having no example
at all.

These tests fail when upstream changes, and the failure says which paragraph
needs rewriting. They are the same idea as the `live` tests in
`literature-tracking`, aimed at a repository rather than an API.

Run with `uv run pytest -m live`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from survey import NONCOMMERCIAL, POINTER, SWALLOWED, TORCH_SPEC, _identify

pytestmark = pytest.mark.live

REPO = "NVIDIA-BioNeMo/Proteina-Complexa"
RAW = "https://raw.githubusercontent.com/" + REPO + "/{branch}/{path}"

# Not `main`. The default branch here is `dev`, and a `master` also answers —
# so the habitual guess 404s. Resolved rather than hard-coded so that a rename
# upstream fails one test with the reason instead of seven with a 404 each.
FALLBACK_BRANCH = "dev"
_branch: list[str] = []


def default_branch() -> str:
    if not _branch:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}",
            headers={"User-Agent": "bio-research-skills/code-reproduction"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                _branch.append(json.load(response).get("default_branch") or FALLBACK_BRANCH)
        except (urllib.error.URLError, ValueError):
            # Unauthenticated GitHub is rate-limited; fall back rather than
            # failing every test for a reason that has nothing to do with them.
            _branch.append(FALLBACK_BRANCH)
    return _branch[0]


def fetch(path: str) -> str:
    url = RAW.format(branch=default_branch(), path=path)
    request = urllib.request.Request(
        url, headers={"User-Agent": "bio-research-skills/code-reproduction"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"{path} is gone from upstream (HTTP {exc.code} at {url}) — "
            "the citation in references/repro-hazards.md needs updating"
        ) from exc


def test_the_default_branch_is_still_not_main():
    """Documented in the hazards file as the first thing that goes wrong."""
    assert default_branch() != "main", "upstream renamed its default branch to main"


def test_the_root_licence_still_carries_no_terms():
    """The flagship example for "a repository's LICENSE is not its licence"."""
    text = fetch("LICENSE")
    assert _identify(text) == [], f"upstream now states terms in LICENSE: {text[:200]}"
    assert POINTER.search(text), "LICENSE no longer forwards elsewhere"


def test_the_weights_are_still_under_a_different_licence_from_the_code():
    code = _identify(fetch("licenses/license_code.txt"))
    weights = _identify(fetch("licenses/license_weights.txt"))
    assert "Apache-2.0" in code
    assert "NVIDIA Open Model License" in weights
    assert set(code) != set(weights)


def test_the_open_model_licence_still_grants_commercial_use():
    """Guards the false positive, against the real text rather than a fixture."""
    text = fetch("licenses/license_weights.txt")
    assert "commercially usable" in text.lower()
    assert not NONCOMMERCIAL.search(text), "upstream added a non-commercial clause"


def test_the_third_party_file_still_stacks_several_licences():
    families = _identify(fetch("licenses/license_third_party.txt"))
    assert len(families) > 1, f"only found {families} — the aggregate case may be gone"
    assert "Beer-ware" in families


def test_the_build_script_still_swallows_its_install_failures():
    text = fetch("env/build_uv_env.sh")
    swallowed = [line for line in text.splitlines() if SWALLOWED.search(line) and "install" in line]
    assert len(swallowed) >= 2, f"upstream fixed some of these: {swallowed}"


def test_pytorch_is_still_declared_only_by_the_build_script():
    """`torch` as a substring is not the claim.

    The manifest names `download.pytorch.org` as a wheel index, so a search
    for the bare word finds it and concludes the dependency is declared. What
    matters is whether anything *pins* torch — `TORCH_SPEC`, the same pattern
    the survey uses — and only the shell script does.
    """
    manifest, build = fetch("pyproject.toml"), fetch("env/build_uv_env.sh")
    assert not TORCH_SPEC.search(manifest), "upstream now pins torch in pyproject.toml"
    assert TORCH_SPEC.search(build), "the build script no longer pins torch"


def test_the_repository_still_ships_its_own_agent_skills():
    assert "name: complexa-setup" in fetch(".claude/skills/complexa-setup/SKILL.md")

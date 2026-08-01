"""`skills/` is a delivery, not a working directory.

Everything under `skills/<name>/` is copied verbatim onto a customer's machine
by `npx skills add`, whose exclusion list is `metadata.json`, `.git`,
`__pycache__` and `__pypackages__` — nothing else. So a file left there is a
file shipped, and a path written there is read by a stranger's agent, in a tree
that contains only what was delivered.

Two things went wrong at once when the suites moved from `skills/<name>/tests/`
to `tests/<name>/`:

- Five pointers to those suites were left behind in four shipped files. On the
  old layout they resolved, because the tests shipped alongside them. Now they
  resolve nowhere, for anyone.
- Nothing would notice a test file left behind in `skills/`. `testpaths` no
  longer reaches it, so it would never run, and `npx skills add` would ship it
  anyway — the worst of both.

The rules here are the ones that are mechanically decidable. Deliberately
absent is "every backticked path must resolve": shipped prose legitimately
quotes *other* repositories' trees — `handoff.md` describes the reference files
of skills that live elsewhere, and `repro-hazards.md` reproduces a directory
listing from the upstream repository under review. Those are quotations, not
navigation, and a check that cannot tell the difference would train the next
author to silence it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO / "skills"
TESTS_ROOT = REPO / "tests"

SHIPPED = sorted(p for p in SKILLS_ROOT.rglob("*") if p.is_file() and p.suffix in {".md", ".py"})
SHIPPED_MD = [p for p in SHIPPED if p.suffix == ".md"]

# A markdown link with a relative target. Unlike a backticked mention, a link is
# an instruction to go somewhere, so it has to arrive.
MD_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:|#)([^)\s]+)\)")

# Any citation of this repository's test suite. Tests no longer ship, so such a
# path is always a pointer back here: it must resolve from the repository root,
# which means carrying the skill-name segment.
TEST_PATH = re.compile(r"\btests/[A-Za-z0-9_./-]*[A-Za-z0-9_]")

# Code spans and fenced blocks quote syntax rather than using it. `note-schema.md`
# documents that `[links](…)` are rejected by the renderer, and stripping these
# is what keeps that sentence from reading as a broken link.
FENCED = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def _relative(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _prose(path: Path) -> str:
    """Markdown with its code quoted out, so syntax-as-example is not read as syntax."""
    return INLINE_CODE.sub("", FENCED.sub("", path.read_text(encoding="utf-8")))


def test_there_is_shipped_content_to_check() -> None:
    """Guard against an rglob that matches nothing and passes everything."""
    assert len(SHIPPED_MD) >= 3, f"only {len(SHIPPED_MD)} shipped .md files under {SKILLS_ROOT}"


def test_no_test_files_are_left_inside_a_skill() -> None:
    """A colocated test is now both unrun and shipped.

    `testpaths = ["tests"]` does not reach into `skills/`, so a test file left
    there never executes — while `npx skills add` copies it to every customer.
    Green suite, dead test, bloated delivery, no complaint from anything.
    """
    stray = sorted(
        _relative(p)
        for pattern in ("test_*.py", "conftest.py")
        for p in SKILLS_ROOT.rglob(pattern)
    )
    assert not stray, (
        f"these live inside a shipped skill, so `npx skills add` delivers them to every "
        f"customer and `testpaths` never runs them: {stray}"
    )


def test_every_skill_has_a_test_directory() -> None:
    """Under the old layout a skill without tests was visibly odd; now it is invisible.

    Adding `skills/<name>/` with no `tests/<name>/` leaves its scripts entirely
    uncovered, and the suite reports the same green it did before.
    """
    skills = {p.name for p in SKILLS_ROOT.iterdir() if p.is_dir() and p.name[0] != "."}
    tested = {p.name for p in TESTS_ROOT.iterdir() if p.is_dir() and p.name[0] not in "._"}
    assert not skills - tested, f"skills with no tests/<name>/ directory: {sorted(skills - tested)}"
    assert not tested - skills, (
        f"tests/ directories with no matching skill: {sorted(tested - skills)}"
    )


@pytest.mark.parametrize("shipped", SHIPPED_MD, ids=_relative)
def test_markdown_links_resolve(shipped: Path) -> None:
    """A link promises navigation. Relative ones resolve against their own file."""
    broken = sorted(
        {
            target
            for target in MD_LINK.findall(_prose(shipped))
            if not (shipped.parent / target.split("#", 1)[0]).exists()
        }
    )
    assert not broken, f"{_relative(shipped)} links to files that do not exist: {broken}"


@pytest.mark.parametrize("shipped", SHIPPED, ids=_relative)
def test_test_suite_citations_resolve_from_the_repo_root(shipped: Path) -> None:
    """`tests/test_sources.py` was true until the suites moved. Now it names nothing.

    Citing a test is worth doing — several of these carry a maintenance contract
    ("when upstream fixes this, that test fails and this file needs editing").
    The contract is only followable if the path is right.
    """
    cited = set(TEST_PATH.findall(shipped.read_text(encoding="utf-8")))
    dangling = sorted(path for path in cited if not (REPO / path).exists())
    assert not dangling, (
        f"{_relative(shipped)} cites tests that do not exist: {dangling}. "
        f"Test paths need the skill-name segment — tests/<skill>/test_x.py — "
        f"because the suites live at the repository root, not inside the skill."
    )

"""The frontmatter is the only part of a skill that fails silently.

Everything else in this repository announces its own breakage: a broken script
raises, a broken test goes red. Frontmatter does not. When the YAML in a
SKILL.md fails to parse, Claude Code does not refuse to load the skill — it
loads it with *empty metadata*, every field dropped. The result is a skill with
no name and no description, which therefore matches no user request and is
never invoked. It does not error. It does not warn. It is simply never used
again, and the suite stays green the whole time.

That is not hypothetical. `literature-tracking` shipped to main in exactly that
state, because its `compatibility:` line was an unquoted scalar containing the
substring ``speed: it`` — and a colon-space inside a plain scalar is where YAML
starts reading a nested mapping. 573 tests passed over it.

Each assertion below pins one failure mode that has actually occurred or that
would be invisible if it did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SKILLS = sorted(p for p in (REPO / "skills").glob("*/SKILL.md"))

# A single tool in `allowed-tools`: a bare name, optionally with a Bash filter.
# Crucially it forbids an interior space, which is the whole point — see
# `test_allowed_tools_is_comma_separated`.
TOOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?$")

# Paths the SKILL.md body points at. The trailing character class keeps the
# sentence's full stop out of the captured filename.
REFERENCED = re.compile(r"\b(?:references|scripts|assets|examples)/[A-Za-z0-9_./-]*[A-Za-z0-9_/]")


def _frontmatter(skill: Path) -> str:
    lines = skill.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", (
        f"{skill.relative_to(REPO)} does not open with a `---` frontmatter fence"
    )
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    raise AssertionError(f"{skill.relative_to(REPO)} opens a frontmatter fence that never closes")


def _parsed(skill: Path) -> dict:
    try:
        loaded = yaml.safe_load(_frontmatter(skill))
    except yaml.YAMLError as exc:
        raise AssertionError(
            f"{skill.relative_to(REPO)}: frontmatter is not valid YAML, so Claude Code will "
            f"load this skill with every field dropped and it will never trigger. "
            f"Long prose values belong in a `>-` block scalar, where a colon is just a "
            f"colon.\n{exc}"
        ) from exc
    assert isinstance(loaded, dict), f"{skill.relative_to(REPO)}: frontmatter is not a mapping"
    return loaded


def test_there_are_skills_to_check() -> None:
    """Guard against a glob that silently matches nothing and passes everything."""
    assert SKILLS, "no skills/*/SKILL.md found — the rest of this file would vacuously pass"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
class TestFrontmatter:
    def test_parses_as_yaml(self, skill: Path) -> None:
        _parsed(skill)

    def test_name_matches_its_directory(self, skill: Path) -> None:
        """Skills are addressed by directory; a mismatched `name` splits the identity."""
        assert _parsed(skill).get("name") == skill.parent.name

    def test_description_is_present_and_substantial(self, skill: Path) -> None:
        """The description is the only thing loaded before the skill triggers.

        If it is thin, the skill does not get invoked — which looks exactly like
        the skill not existing.
        """
        description = _parsed(skill).get("description")
        assert isinstance(description, str), "description must be a string"
        assert len(description) >= 200, (
            f"description is {len(description)} chars — too thin to match a request on"
        )

    def test_allowed_tools_is_comma_separated(self, skill: Path) -> None:
        """`Bash Read Write` is one tool named "Bash Read Write", and it exists nowhere.

        Claude Code's schema reads this field as a "comma-separated string or
        YAML list", so a space-separated string survives parsing, names nothing,
        and quietly leaves the skill unable to run its own scripts.
        """
        declared = _parsed(skill).get("allowed-tools")
        if declared is None:
            return
        tools = declared.split(",") if isinstance(declared, str) else declared
        for tool in tools:
            assert TOOL.match(tool.strip()), (
                f"{tool.strip()!r} is not a tool name. Separate tools with commas, "
                f"not spaces — got {declared!r}"
            )

    def test_every_path_the_body_points_at_exists(self, skill: Path) -> None:
        """A dead pointer costs a tool call and teaches Claude the file is absent."""
        body = skill.read_text(encoding="utf-8")
        missing = sorted(
            {ref for ref in REFERENCED.findall(body) if not (skill.parent / ref).exists()}
        )
        assert not missing, f"{skill.parent.name} points at files that do not exist: {missing}"

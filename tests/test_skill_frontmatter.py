"""Frontmatter fails quietly, and how quietly depends on which way it breaks.

Everything else in this repository announces its own breakage: a broken script
raises, a broken test goes red. Frontmatter degrades instead, and the degrees
were measured rather than assumed — by loading deliberately damaged skills with
``claude --plugin-dir`` and reading the always-on token cost the runtime
reports for each, which is the metadata it actually kept:

===========================  ==========  =====================================
frontmatter                  always-on   what the runtime did
===========================  ==========  =====================================
valid, ~290-char description    ~90 tok   baseline
``compatibility: …speed: it``  ~240 tok   **repaired.** Claude Code retries a
                                          failed parse with a fixup pass that
                                          targets exactly this, and recovers
                                          every field
plain scalar containing ` #`    ~30 tok   **truncated.** 260 characters and 12
                                          trigger phrases gone; not repaired,
                                          not reported
not a mapping at all           <20 tok    **dropped.** No name, no description
no ``description:`` key        <20 tok    the control: this is what dropped
                                          metadata costs
===========================  ==========  =====================================

Two things follow, and they are why this file is shaped the way it is.

The colon-space bug that prompted it — ``literature-tracking``'s
``compatibility:`` line reading ``…rather than speed: it raises…`` — was
**survivable**. The runtime repaired it. It is still worth refusing, because
relying on an undocumented fixup pass is not a plan, but it never broke that
skill and this file should not claim it did.

The hazard that is genuinely silent is the one nobody was looking for: in a
plain scalar, ` #` starts a comment and eats the rest of the line. Same field,
same file, no error, no repair — just a description that quietly stops carrying
the phrases that make the skill fire. A ``>-`` block scalar closes both, which
is why `test_prose_fields_use_a_block_scalar` pins the shape rather than
hunting for the characters.

These checks are deliberately stricter than the runtime: PyYAML has no fixup
pass, so frontmatter that only survives repair fails here. That is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO / "skills"

# Every directory that claims to be a skill, and every SKILL.md found under one.
# Pinned against each other in `test_the_skill_set_is_exactly_what_is_checked`,
# because a glob that quietly matches one fewer file takes its tests with it.
SKILL_DIRS = sorted(p.name for p in SKILLS_ROOT.iterdir() if p.is_dir() and p.name[0] != ".")
SKILLS = sorted(SKILLS_ROOT.glob("*/SKILL.md"))

# Long prose fields. These are the ones that grow by accretion — another trigger
# phrase, another caveat — so they are the ones that eventually acquire a `:` or
# a `#`. See `test_prose_fields_use_a_block_scalar`.
PROSE_FIELDS = ("description", "compatibility")
BLOCK_SCALAR = (">", ">-", ">+", "|", "|-", "|+")

# Claude Code truncates a skill description past this many characters when it
# builds the listing it shows the model ("Per-skill description character cap in
# the skill listing sent to Claude (default: 1536). Descriptions longer than
# this are truncated."). Truncation takes the tail, and the tail is where the
# trigger phrases live.
DESCRIPTION_CAP = 1536

# A single tool in `allowed-tools`: a bare name, optionally with a Bash filter.
# Spaces are fine *inside* the filter — `Bash(gh pr *)` is a real, valid token —
# and rejected outside it, which is what catches `Bash Read Write` and a YAML
# list holding one such string.
#
# Known limitation: the comma split below shatters a filter that contains a
# comma, e.g. `Bash(git add,git commit)`. Nothing here uses one; if something
# does, split on top-level commas instead of widening this pattern.
TOOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?$")

# Tool names Claude Code ships. A misspelling here is exactly as silent as a
# missing comma: it names nothing and reports nothing. MCP tools carry a
# `mcp__server__tool` shape and are allowed through unchecked, since the servers
# available to a customer cannot be enumerated from here.
KNOWN_TOOLS = frozenset(
    {
        "Agent", "AskUserQuestion", "Bash", "Edit", "Glob", "Grep", "NotebookEdit",
        "Read", "Skill", "SlashCommand", "Task", "TodoWrite", "WebFetch", "WebSearch",
        "Write",
    }
)

# Paths the SKILL.md body points at. The trailing character class keeps the
# sentence's full stop out of the captured filename. Bare `tests/` paths are
# deliberately absent — those are checked across all shipped content, from the
# repository root, in test_shipped_content.py.
REFERENCED = re.compile(r"\b(?:references|scripts|assets|examples)/[A-Za-z0-9_./-]*[A-Za-z0-9_/]")
URL = re.compile(r"https?://\S+")

QUOTED_TRIGGER = re.compile(r'"[^"]+"')
THIRD_PERSON = "This skill should be used when"


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
            f"{skill.relative_to(REPO)}: frontmatter is not valid YAML. Claude Code may "
            f"recover it with its fixup pass, or may load the skill with every field "
            f"dropped — which of the two depends on the damage, and neither is worth "
            f"relying on. Long prose values belong in a `>-` block scalar, where a colon "
            f"is just a colon.\n{exc}"
        ) from exc
    assert isinstance(loaded, dict), f"{skill.relative_to(REPO)}: frontmatter is not a mapping"
    return loaded


def test_the_skill_set_is_exactly_what_is_checked() -> None:
    """Pin the parametrize set, not merely its truthiness.

    Asserting `SKILLS` is non-empty catches a glob that matches nothing. It does
    not catch a glob that matches one fewer — rename a `SKILL.md` and this file
    quietly drops five checks and still reports green. Nor does `*/SKILL.md`
    descend, so a skill nested one level deeper is exempt from everything here.
    """
    assert SKILL_DIRS, f"no skill directories under {SKILLS_ROOT}"
    assert [p.parent.name for p in SKILLS] == SKILL_DIRS, (
        f"every skills/ subdirectory needs a SKILL.md directly inside it. "
        f"Directories: {SKILL_DIRS}. With a SKILL.md: {[p.parent.name for p in SKILLS]}"
    )
    nested = sorted(
        p.relative_to(REPO).as_posix() for p in SKILLS_ROOT.rglob("SKILL.md") if p not in SKILLS
    )
    assert not nested, f"these are too deep for `*/SKILL.md` and go unchecked: {nested}"


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
class TestFrontmatter:
    def test_parses_as_yaml(self, skill: Path) -> None:
        _parsed(skill)

    def test_name_matches_its_directory(self, skill: Path) -> None:
        """Skills are addressed by directory; a mismatched `name` splits the identity."""
        assert _parsed(skill).get("name") == skill.parent.name

    def test_prose_fields_use_a_block_scalar(self, skill: Path) -> None:
        """Pin the class, not the instance that happened to be found.

        `test_parses_as_yaml` only fires once a plain scalar *already* contains
        a colon-space. It cannot see a plain scalar that is one edit away — and
        these fields grow by accretion, so there will be another edit.

        A second failure mode is invisible to the parse check entirely: in a
        plain scalar, ` #` starts a comment and silently eats the rest of the
        line. A description can lose its trailing trigger phrases, still parse,
        and still look substantial.

        Inside `>-`, a colon is a colon and a hash is a hash.
        """
        for line in _frontmatter(skill).splitlines():
            for field in PROSE_FIELDS:
                if not line.startswith(f"{field}:"):
                    continue
                marker = line.split(":", 1)[1].strip()
                assert marker in BLOCK_SCALAR, (
                    f"{skill.relative_to(REPO)}: `{field}` is a plain scalar. Long prose "
                    f"belongs in a `>-` block, where `:` cannot open a mapping and ` #` "
                    f"cannot start a comment. Got: {marker[:40]!r}…"
                )

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

    def test_description_keeps_its_trigger_phrases(self, skill: Path) -> None:
        """Length is a proxy. The quoted phrases are the thing that does the matching.

        A description can shed most of its triggers and stay well above any
        length floor — which is precisely what a truncating ` #` comment, or an
        over-zealous edit, would do.
        """
        description = _parsed(skill)["description"]
        triggers = QUOTED_TRIGGER.findall(description)
        assert len(triggers) >= 3, f"only {len(triggers)} quoted trigger phrases: {triggers}"
        assert description.startswith(THIRD_PERSON), (
            f"the repo writes descriptions in the third person, opening {THIRD_PERSON!r}. "
            f"Got: {description[:60]!r}…"
        )

    def test_description_fits_under_the_truncation_cap(self, skill: Path) -> None:
        """Over the cap, Claude Code shortens it rather than complaining.

        The trigger phrases sit at the end of every description in this repo, so
        the part silently dropped is the part that does the matching.
        """
        length = len(_parsed(skill)["description"])
        assert length < DESCRIPTION_CAP, (
            f"description is {length} chars; Claude Code truncates past {DESCRIPTION_CAP} "
            f"and the tail is where the trigger phrases are"
        )

    def test_allowed_tools_is_declared_under_the_name_that_is_read(self, skill: Path) -> None:
        """A near-miss key is dropped in silence, and the restriction never applies.

        This is the one field where absence is legal, so an early return on
        `None` would let `allowed_tools`, `allowedTools` or `allowedtools` pass
        as "not declared" — re-opening the exact bug this file was written for,
        via a one-character mistake, in the guard against a one-character
        mistake.
        """
        keys = list(_parsed(skill))
        lookalikes = [k for k in keys if k != "allowed-tools" and _slug(k) == "allowedtools"]
        assert not lookalikes, (
            f"{lookalikes} — Claude Code reads only `allowed-tools`. Any other spelling is "
            f"dropped and the skill silently runs with every tool available."
        )
        assert "allowed-tools" in keys, (
            f"every skill here declares allowed-tools; got keys {sorted(keys)}"
        )

    def test_allowed_tools_names_real_tools(self, skill: Path) -> None:
        """Comma-separated, and every name a tool that exists.

        The separator is a convention, not a repair: Claude Code documents this
        field as a "comma-separated string or YAML list" and every skill in the
        official plugins writes it that way, but whether its tokenizer *also*
        tolerates spaces was not established here, so this is house style rather
        than a bug being fixed.

        The identity check is the part that catches a real silent failure. A
        misspelled tool — `Raed` — is accepted by any shape check, names
        nothing, grants nothing, and reports nothing.
        """
        declared = _parsed(skill)["allowed-tools"]
        assert declared, "`allowed-tools:` with no value declares nothing and grants nothing"
        tools = declared.split(",") if isinstance(declared, str) else declared
        for raw in tools:
            tool = raw.strip()
            assert TOOL.match(tool), (
                f"{tool!r} is not a tool name. Separate tools with commas, not spaces — "
                f"got {declared!r}"
            )
            name = tool.split("(", 1)[0]
            assert name in KNOWN_TOOLS or name.startswith("mcp__"), (
                f"{name!r} is not a Claude Code tool, so it grants nothing and says nothing. "
                f"Known: {sorted(KNOWN_TOOLS)}"
            )

    def test_every_path_the_body_points_at_exists(self, skill: Path) -> None:
        """A dead pointer costs a tool call and teaches Claude the file is absent."""
        body = URL.sub("", skill.read_text(encoding="utf-8"))
        pointers = set(REFERENCED.findall(body))
        assert pointers, (
            "no references/ or scripts/ pointer found in this SKILL.md — either the body "
            "stopped naming its own resources, or REFERENCED no longer matches them and "
            "this check has been passing on an empty set"
        )
        missing = sorted(ref for ref in pointers if not (skill.parent / ref).is_file())
        # `scripts/sources/` is a package, cited as a directory on purpose.
        missing = [ref for ref in missing if not (skill.parent / ref).is_dir()]
        assert not missing, f"{skill.parent.name} points at files that do not exist: {missing}"


def _slug(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()

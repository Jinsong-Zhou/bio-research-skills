#!/usr/bin/env python3
"""The contract between the agent's reading and the document that gets written.

A deep read is judgement, and judgement is not scriptable — so this file does
not attempt it. What it does is hold the agent to a shape: every claim it
credits to the paper has to name where in the paper that claim lives, and a
section it could not honestly fill has to say so rather than quietly read as
though it were filled.

    python3 note.py template            # the skeleton to fill in
    python3 note.py validate note.json  # what is missing or ungrounded
    python3 note.py render note.json    # Markdown, the format that always works

Markdown is the fallback, not the goal. Word and slides are rendered by the
``docx`` and ``pptx`` skills from ``anthropics/skills``; see SKILL.md. Keeping
the note itself in JSON is what lets all three exist without one of them being
the source of truth.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: A reference into the paper: a figure, table, section, equation or page.
#: Matched in English and Chinese because the note follows the user's language
#: while these anchors usually stay in the paper's.
ANCHOR = re.compile(
    r"(?:"
    r"Fig(?:ure)?\.?\s*\d|Tab(?:le)?\.?\s*\d|Sec(?:tion)?\.?\s*\d|Eq(?:uation)?\.?\s*\d"
    r"|pp?\.\s*\d|Appendix|Supplement(?:ary|al)?|Extended\s+Data"
    r"|[图表]\s*\d|第\s*\d+\s*[节章页]|附录|补充材料|扩展数据"
    r")",
    re.IGNORECASE,
)

DECISIONS = ("follow-up", "watch", "skip")
CONFIDENCES = ("high", "medium", "low")
RELEVANCE_STATES = ("written", "no-background-provided")
FULLTEXT_STATES = ("full", "abstract-only")

UNDERSTANDING_FIELDS = ("problem", "method", "experiments", "findings")


class NoteError(RuntimeError):
    """The note is not usable as written."""


TEMPLATE: dict[str, Any] = {
    "language": "en",
    "paper": {
        "title": "",
        "authors": [],
        "year": None,
        "venue": "",
        "doi": "",
        "url": "",
        "fulltext": "full",
    },
    "understanding": {
        "problem": "",
        "method": "",
        "experiments": "",
        "findings": "",
    },
    "assessment": {
        "claims": [
            {"claim": "", "evidence": "", "confidence": "medium", "issue": ""},
        ],
        "limitations": {"acknowledged": [], "unstated": []},
        "verdict": {"decision": "watch", "reasoning": "", "cost": "", "next_steps": []},
    },
    "relevance": {"status": "no-background-provided", "text": ""},
}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def validate(note: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)``.

    Errors mean the note is not renderable. Warnings mean it is, but something
    about it should be said out loud in the document.
    """
    errors: list[str] = []
    warnings: list[str] = []

    paper = note.get("paper") or {}
    if _blank(paper.get("title")):
        errors.append("paper.title is empty")
    fulltext = paper.get("fulltext")
    if fulltext not in FULLTEXT_STATES:
        errors.append(f"paper.fulltext must be one of {FULLTEXT_STATES}, got {fulltext!r}")
    elif fulltext == "abstract-only":
        warnings.append(
            "paper.fulltext is 'abstract-only' — the assessment cannot be grounded "
            "in figures or methods, and the document will say so"
        )

    understanding = note.get("understanding") or {}
    for field in UNDERSTANDING_FIELDS:
        if _blank(understanding.get(field)):
            errors.append(f"understanding.{field} is empty")

    findings = understanding.get("findings") or ""
    if findings and not ANCHOR.search(findings):
        warnings.append(
            "understanding.findings cites no figure, table or section — results "
            "described without a pointer are hard to check later"
        )

    errors.extend(_validate_assessment(note.get("assessment") or {}, fulltext))
    errors.extend(_validate_relevance(note.get("relevance") or {}))
    return errors, warnings


def _validate_assessment(assessment: dict[str, Any], fulltext: Any) -> list[str]:
    errors: list[str] = []

    claims = assessment.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("assessment.claims is empty — a deep read has to weigh at least one claim")
    else:
        for i, claim in enumerate(claims):
            errors.extend(_validate_claim(i, claim, fulltext))

    verdict = assessment.get("verdict") or {}
    decision = verdict.get("decision")
    if decision not in DECISIONS:
        errors.append(f"assessment.verdict.decision must be one of {DECISIONS}, got {decision!r}")
    if _blank(verdict.get("reasoning")):
        errors.append(
            "assessment.verdict.reasoning is empty — a verdict without one is a coin flip"
        )

    limitations = assessment.get("limitations") or {}
    for key in ("acknowledged", "unstated"):
        if not isinstance(limitations.get(key), list):
            errors.append(f"assessment.limitations.{key} must be a list")

    return errors


def _validate_claim(index: int, claim: Any, fulltext: Any) -> list[str]:
    where = f"assessment.claims[{index}]"
    if not isinstance(claim, dict):
        return [f"{where} must be an object"]

    errors: list[str] = []
    if _blank(claim.get("claim")):
        errors.append(f"{where}.claim is empty")
    if claim.get("confidence") not in CONFIDENCES:
        errors.append(f"{where}.confidence must be one of {CONFIDENCES}")

    evidence = claim.get("evidence")
    issue = claim.get("issue")
    if _blank(evidence):
        # No evidence is a legitimate — and often the most interesting —
        # finding. It just has to be stated as one rather than left blank.
        if _blank(issue):
            errors.append(
                f"{where} has neither evidence nor an issue. If the paper does not "
                "back this claim, say that in .issue; do not leave both empty"
            )
    elif fulltext == "full" and not ANCHOR.search(str(evidence)):
        errors.append(
            f"{where}.evidence does not name a figure, table, section or page "
            f"({evidence!r}). Point at the paper, not at the paper's summary"
        )

    return errors


def _validate_relevance(relevance: dict[str, Any]) -> list[str]:
    status = relevance.get("status")
    if status not in RELEVANCE_STATES:
        return [f"relevance.status must be one of {RELEVANCE_STATES}, got {status!r}"]
    if status == "written" and _blank(relevance.get("text")):
        return ["relevance.status is 'written' but relevance.text is empty"]
    return []


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Deep read: {title}",
        "paper": "The paper",
        "part1": "Part 1 — What it does",
        "problem": "The problem",
        "method": "The method",
        "experiments": "The experiments",
        "findings": "What they found",
        "part2": "Part 2 — Assessment",
        "claims": "Claim by claim",
        "col_claim": "Claim",
        "col_evidence": "Evidence",
        "col_confidence": "Confidence",
        "col_issue": "Issue",
        "no_evidence": "none given",
        "limitations": "Limitations",
        "acknowledged": "Acknowledged by the authors",
        "unstated": "Not stated in the paper",
        "verdict": "Verdict",
        "cost": "Cost to act on it",
        "next_steps": "Next steps",
        "relevance": "Relevance to your work",
        "no_background": "_No research background was provided, so this section is "
        "left empty rather than guessed at._",
        "abstract_banner": "> ⚠️ **Abstract only.** No full text was available, so nothing "
        "below rests on the paper's actual figures, tables or methods. This is a "
        "summary, not a deep read.",
        "authors": "Authors",
        "venue": "Venue",
        "decision_follow-up": "Worth following up",
        "decision_watch": "Worth watching",
        "decision_skip": "Can skip",
        "confidence_high": "high",
        "confidence_medium": "medium",
        "confidence_low": "low",
    },
    "zh": {
        "title": "精读：{title}",
        "paper": "论文信息",
        "part1": "第一部分 — 这篇做了什么",
        "problem": "问题",
        "method": "方法",
        "experiments": "实验",
        "findings": "结果",
        "part2": "第二部分 — 评价",
        "claims": "逐条审查主张",
        "col_claim": "主张",
        "col_evidence": "证据",
        "col_confidence": "可信度",
        "col_issue": "问题",
        "no_evidence": "未给出",
        "limitations": "局限",
        "acknowledged": "作者自己承认的",
        "unstated": "论文没说的",
        "verdict": "结论：要不要跟进",
        "cost": "跟进成本",
        "next_steps": "下一步",
        "relevance": "与你的工作的关联",
        "no_background": "_未提供研究背景，此节留空，不做编造。_",
        "abstract_banner": "> ⚠️ **仅有摘要。** 没拿到全文，下面没有任何一句建立在论文实际的"
        "图表和方法上。这是摘要，不是精读。",
        "authors": "作者",
        "venue": "发表于",
        "decision_follow-up": "值得跟进",
        "decision_watch": "值得观望",
        "decision_skip": "可以跳过",
        "confidence_high": "高",
        "confidence_medium": "中",
        "confidence_low": "低",
    },
}


def _cell(value: Any, fallback: str = "—") -> str:
    """Escape a value for a Markdown table cell."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return fallback
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(note: dict[str, Any]) -> str:
    lang = note.get("language", "en")
    s = STRINGS.get(lang, STRINGS["en"])
    paper = note.get("paper") or {}
    lines: list[str] = [f"# {s['title'].format(title=paper.get('title', ''))}", ""]

    if paper.get("fulltext") == "abstract-only":
        lines += [s["abstract_banner"], ""]

    lines += [f"## {s['paper']}", ""]
    authors = paper.get("authors") or []
    if authors:
        shown = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        lines.append(f"- **{s['authors']}**: {shown}")
    for label, key in ((s["venue"], "venue"), ("DOI", "doi"), ("URL", "url")):
        if paper.get(key):
            lines.append(f"- **{label}**: {paper[key]}")
    lines.append("")

    understanding = note.get("understanding") or {}
    lines += [f"## {s['part1']}", ""]
    for field in UNDERSTANDING_FIELDS:
        lines += [f"### {s[field]}", "", str(understanding.get(field, "")).strip(), ""]

    lines += _render_assessment(note.get("assessment") or {}, s)
    lines += _render_relevance(note.get("relevance") or {}, s)
    return "\n".join(lines).rstrip() + "\n"


def _render_assessment(assessment: dict[str, Any], s: dict[str, str]) -> list[str]:
    lines = [f"## {s['part2']}", "", f"### {s['claims']}", ""]
    columns = (s["col_claim"], s["col_evidence"], s["col_confidence"], s["col_issue"])
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|---|---|---|---|")
    for claim in assessment.get("claims") or []:
        confidence = claim.get("confidence", "")
        lines.append(
            f"| {_cell(claim.get('claim'))} "
            f"| {_cell(claim.get('evidence'), s['no_evidence'])} "
            f"| {s.get(f'confidence_{confidence}', confidence)} "
            f"| {_cell(claim.get('issue'))} |"
        )
    lines.append("")

    limitations = assessment.get("limitations") or {}
    lines += [f"### {s['limitations']}", ""]
    for label, key in ((s["acknowledged"], "acknowledged"), (s["unstated"], "unstated")):
        items = limitations.get(key) or []
        lines.append(f"**{label}**")
        lines.append("")
        lines += [f"- {item}" for item in items] or ["- —"]
        lines.append("")

    verdict = assessment.get("verdict") or {}
    decision = verdict.get("decision", "")
    lines += [
        f"### {s['verdict']}",
        "",
        f"**{s.get(f'decision_{decision}', decision)}** — {verdict.get('reasoning', '')}",
        "",
    ]
    if verdict.get("cost"):
        lines += [f"**{s['cost']}**: {verdict['cost']}", ""]
    if verdict.get("next_steps"):
        lines += [f"**{s['next_steps']}**", ""]
        lines += [f"{i}. {step}" for i, step in enumerate(verdict["next_steps"], 1)]
        lines.append("")
    return lines


def _render_relevance(relevance: dict[str, Any], s: dict[str, str]) -> list[str]:
    lines = [f"## {s['relevance']}", ""]
    if relevance.get("status") == "written":
        lines += [str(relevance.get("text", "")).strip(), ""]
    else:
        lines += [s["no_background"], ""]
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoteError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise NoteError(f"{path} is not a JSON object")
    return data


def _report(errors: list[str], warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and render a deep-reading note.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("template", help="print an empty note skeleton")

    validate_cmd = sub.add_parser("validate", help="check a note for gaps and ungrounded claims")
    validate_cmd.add_argument("note", type=Path)

    render_cmd = sub.add_parser("render", help="render a note to Markdown")
    render_cmd.add_argument("note", type=Path)
    render_cmd.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    render_cmd.add_argument(
        "--force",
        action="store_true",
        help="render even if validation fails (the gaps stay visible in the output)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "template":
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        return 0

    try:
        note = load(args.note)
    except NoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    errors, warnings = validate(note)

    if args.command == "validate":
        _report(errors, warnings)
        if not errors:
            print("ok", file=sys.stderr)
        return 1 if errors else 0

    if errors and not args.force:
        _report(errors, warnings)
        print("refusing to render; fix the above or pass --force", file=sys.stderr)
        return 1

    _report([], warnings)
    markdown = render_markdown(note)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

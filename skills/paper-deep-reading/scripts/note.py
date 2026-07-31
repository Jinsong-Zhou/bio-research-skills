#!/usr/bin/env python3
"""The contract between the agent's reading and the document that gets written.

A deep read is judgement, and judgement is not scriptable — so this file does
not attempt it. What it does is hold the agent to a shape: every claim it
credits to the paper has to name where in the paper that claim lives, and a
section it could not honestly fill has to say so rather than quietly read as
though it were filled.

    python3 note.py template                       # the skeleton to fill in
    python3 note.py validate note.json             # what is missing or ungrounded
    python3 note.py render note.json               # Markdown, always available
    python3 note.py render note.json --format blocks   # for a non-Python renderer

Word and slides are rendered by the ``docx`` and ``pptx`` skills from
``anthropics/skills``, which are not written in Python — so ``--format blocks``
hands them a typed document tree with the headings already in the note's
language, rather than Markdown they would have to parse a table out of. The
note stays the source of truth and none of the three renderings owns it.
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
#:
#: The optional ``S``/``E`` before the number is not decoration: ``Fig. S3``
#: and ``Table S1`` are how journals cite supplementary material, and SKILL.md
#: step 3.6 sends the agent there specifically because that is where a paper's
#: weakest point usually sits. Demanding a digit immediately after the keyword
#: rejected the ordinary way of writing the citation.
ANCHOR = re.compile(
    r"(?:"
    r"Fig(?:ure)?s?\.?\s*[SE]?\d|Tab(?:le)?s?\.?\s*[SE]?\d"
    r"|Sec(?:tion)?s?\.?\s*\d|Eq(?:uation)?s?\.?\s*\d"
    r"|pp?\.\s*\d|Appendix|Supplement(?:ary|al)?|Extended\s+Data"
    r"|[图表]\s*[SE]?\d|第\s*\d+\s*[节章页]|附录|补充材料|扩展数据"
    r")",
    re.IGNORECASE,
)

DECISIONS = ("follow-up", "watch", "skip")
CONFIDENCES = ("high", "medium", "low")
RELEVANCE_STATES = ("written", "no-background-provided")
FULLTEXT_STATES = ("full", "abstract-only")

#: Languages with a heading table. Validated rather than defaulted silently:
#: ``STRINGS.get(lang, STRINGS["en"])`` means ``"zh-CN"`` renders an entirely
#: English scaffold around Chinese content, and nothing says so.
LANGUAGES = ("en", "zh")

#: Every shape ``build_blocks`` may emit. Named once here because four places
#: used to carry their own copy of the list — this docstring, SKILL.md step 8,
#: ``references/note-schema.md`` and the test suite — and a renderer that
#: silently drops an unknown type is the failure they were meant to prevent.
BLOCK_TYPES = (
    "title",
    "banner",
    "h1",
    "h2",
    "p",
    "note",
    "label",
    "fields",
    "bullets",
    "numbered",
    "table",
)

#: What kind of paper this is. It decides how ``pipeline`` decomposes — a
#: model has training and inference, a cryo-EM structure has sample prep and
#: reconstruction, and asking for the wrong one produces a section that
#: describes a paper nobody wrote. See SKILL.md step 4.
PAPER_TYPES = ("computational", "experimental", "method", "resource", "theory")

#: The teaching half, in the order it should be written. Each answers a
#: question the previous one raises: what is hard → what is the idea → what
#: does it do concretely → why does that work → what came out.
UNDERSTANDING_FIELDS = ("problem", "approach", "pipeline", "mechanism", "findings")

#: Fields where a one-liner means the paper was summarised rather than read.
#: ``findings`` is exempt: a headline number is legitimately short.
DEPTH_FIELDS = ("problem", "approach", "pipeline", "mechanism")

#: Weighted-character floor below which a field warns. CJK carries roughly
#: twice the information per character, so it counts double and the same
#: threshold works for both languages. Deliberately low — this catches
#: "the method improves accuracy" and nothing subtler.
MIN_DEPTH = 180


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
        "type": "experimental",
        "fulltext": "full",
    },
    "understanding": {
        "problem": "",
        "approach": "",
        "pipeline": "",
        "mechanism": "",
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


def _mapping(value: Any) -> dict[str, Any]:
    """``value`` if it is a mapping, an empty one otherwise.

    Validation reports a section written as the wrong shape; it must not die
    on it. A tool whose job is to say what is wrong with a note is worth
    nothing if a malformed note gives it a traceback instead of a report.
    """
    return value if isinstance(value, dict) else {}


def _string_list_errors(value: Any, where: str) -> list[str]:
    """Reject a string written where a list belongs.

    ``list("watch for a code release")`` is legal Python, so an unchecked
    string reaches the renderer and comes out one character per item. The note
    is authored as JSON by hand, which makes this the likeliest mistake in it,
    and until now it was one of the few that validation waved through.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return [
            f"{where} must be a list of strings, not {type(value).__name__} — "
            "a string here renders one character per item"
        ]
    return [
        f"{where}[{i}] must be a string, not {type(item).__name__}"
        for i, item in enumerate(value)
        if not isinstance(item, str)
    ]


def _depth(text: str) -> int:
    """Weighted length: CJK characters count double.

    A Chinese sentence carries about twice the information of an English one
    of the same character count, so a single threshold over raw ``len`` would
    demand three paragraphs of English and one line of Chinese.
    """
    return len(text) + sum(1 for ch in text if "一" <= ch <= "鿿")


def validate(note: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)``.

    Errors mean the note is not renderable. Warnings mean it is, but something
    about it should be said out loud in the document.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("paper", "understanding", "assessment", "relevance"):
        section = note.get(key)
        if section is not None and not isinstance(section, dict):
            errors.append(f"{key} must be an object, not {type(section).__name__}")

    language = note.get("language") or "en"
    if language not in LANGUAGES:
        errors.append(
            f"language must be one of {LANGUAGES}, got {language!r} — an unrecognised "
            "value renders English headings over the note's own language without saying so"
        )

    paper = _mapping(note.get("paper"))
    if _blank(paper.get("title")):
        errors.append("paper.title is empty")
    errors.extend(_string_list_errors(paper.get("authors"), "paper.authors"))
    if paper.get("type") not in PAPER_TYPES:
        errors.append(
            f"paper.type must be one of {PAPER_TYPES}, got {paper.get('type')!r} — "
            "it decides how the pipeline section decomposes"
        )
    fulltext = paper.get("fulltext")
    if fulltext not in FULLTEXT_STATES:
        errors.append(f"paper.fulltext must be one of {FULLTEXT_STATES}, got {fulltext!r}")
    elif fulltext == "abstract-only":
        warnings.append(
            "paper.fulltext is 'abstract-only' — the assessment cannot be grounded "
            "in figures or methods, and the document will say so"
        )

    understanding = _mapping(note.get("understanding"))
    for field in UNDERSTANDING_FIELDS:
        value = understanding.get(field)
        if _blank(value):
            errors.append(f"understanding.{field} is empty")
        elif not isinstance(value, str):
            errors.append(
                f"understanding.{field} must be a string, not {type(value).__name__} — "
                "use a blank line for a new paragraph"
            )

    for field in DEPTH_FIELDS:
        text = understanding.get(field)
        text = text if isinstance(text, str) else ""
        if text and _depth(text) < MIN_DEPTH:
            warnings.append(
                f"understanding.{field} is {_depth(text)} weighted characters, under "
                f"{MIN_DEPTH}. A deep read explains; a summary states. Say why it is "
                "hard, not just what it is"
            )

    findings = understanding.get("findings")
    findings = findings if isinstance(findings, str) else ""
    if findings and not ANCHOR.search(findings):
        warnings.append(
            "understanding.findings cites no figure, table or section — results "
            "described without a pointer are hard to check later"
        )

    errors.extend(_validate_assessment(_mapping(note.get("assessment")), fulltext))
    errors.extend(_validate_relevance(_mapping(note.get("relevance"))))
    errors.extend(_validate_no_markup(note))
    return errors, warnings


#: Significance notation, cleared before the markup check runs. A run of
#: asterisks that closes a parenthetical (``(**, p < 0.01)``, ``(***)``) or
#: introduces a p-value (``***p < 0.001``) is how every experimental paper
#: reports its statistics — flagging it as Markdown was flagging the domain.
#:
#: The lookbehind is what keeps real emphasis intact: the closing ``**`` of
#: ``**doubled**`` follows a word character, so it is never cleared, and the
#: pair still matches below. Matching balanced emphasis directly cannot work
#: here — in ``(**, p<0.05) 与 (***, p<0.001)`` a paired pattern bridges the
#: two runs and reports the text between them as bold.
_SIGNIFICANCE = re.compile(
    r"(?<![^\s(\[{])\*{1,4}(?=\s*(?:[,;)\]]|p\s*[<>=≤≥]|$))",
    re.IGNORECASE,
)

#: Markdown that shows up as literal characters in Word. Emphasis is the
#: renderer's job; the note carries text.
_MARKUP = (
    # Paired delimiters only — a bare run of asterisks is statistics, not bold.
    (re.compile(r"\*\*(?=[^\s*])[^*]+\*\*"), "bold markers (**…**)"),
    (re.compile(r"(?<!\w)__(?=[^\s_])[^_]+__(?!\w)"), "bold markers (__…__)"),
    (re.compile(r"`"), "backticks"),
    (re.compile(r"^\s*#{1,6}\s", re.MULTILINE), "a Markdown heading"),
    (re.compile(r"^\s*>\s", re.MULTILINE), "a blockquote marker"),
    (re.compile(r"^\s*[-*+]\s", re.MULTILINE), "a bullet marker"),
    (re.compile(r"\[[^\]\n]+\]\([^)\n]+\)"), "a Markdown link"),
    (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), "a Markdown table row"),
    (re.compile(r"</?[A-Za-z][A-Za-z0-9]*\s*/?>"), "an HTML tag"),
)


def _walk_text(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [t for k, v in value.items() for t in _walk_text(v, f"{path}.{k}" if path else k)]
    if isinstance(value, list):
        return [t for i, v in enumerate(value) for t in _walk_text(v, f"{path}[{i}]")]
    return []


def _validate_no_markup(note: dict[str, Any]) -> list[str]:
    """Reject Markdown syntax anywhere in the note's text.

    Markdown renders it; Word shows it verbatim. Since both come from the same
    note, anything that only works in one of them is a defect. Paragraph breaks
    are the supported structure — a blank line, which ``paragraphs()`` turns
    into separate blocks.
    """
    errors = []
    for path, text in _walk_text(note):
        text = _SIGNIFICANCE.sub("", text)
        for pattern, what in _MARKUP:
            if pattern.search(text):
                errors.append(
                    f"{path} contains {what}. The note carries text, not Markdown — Word "
                    "renders it literally. Use a blank line for a new paragraph, and put "
                    "list items in the fields that are already lists"
                )
                break
    return errors


def _validate_assessment(assessment: dict[str, Any], fulltext: Any) -> list[str]:
    errors: list[str] = []

    claims = assessment.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("assessment.claims is empty — a deep read has to weigh at least one claim")
    else:
        for i, claim in enumerate(claims):
            errors.extend(_validate_claim(i, claim, fulltext))

    verdict = _mapping(assessment.get("verdict"))
    decision = verdict.get("decision")
    if decision not in DECISIONS:
        errors.append(f"assessment.verdict.decision must be one of {DECISIONS}, got {decision!r}")
    if _blank(verdict.get("reasoning")):
        errors.append(
            "assessment.verdict.reasoning is empty — a verdict without one is a coin flip"
        )
    errors.extend(_string_list_errors(verdict.get("next_steps"), "assessment.verdict.next_steps"))

    limitations = _mapping(assessment.get("limitations"))
    for key in ("acknowledged", "unstated"):
        value = limitations.get(key)
        if not isinstance(value, list):
            errors.append(f"assessment.limitations.{key} must be a list")
        else:
            errors.extend(_string_list_errors(value, f"assessment.limitations.{key}"))

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
        "problem": "The problem, and why it is hard",
        "approach": "The idea, and how it answers that problem",
        "pipeline": "What it actually does, step by step",
        "mechanism": "The mechanism that carries the result",
        "findings": "What came out",
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
        "no_background": "No research background was provided, so this section is "
        "left empty rather than guessed at.",
        "banner_lead": "⚠️ Abstract only.",
        "banner_body": "No full text was available, so nothing below rests on the "
        "paper's actual figures, tables or methods. This is a summary, not a deep read.",
        "authors": "Authors",
        "venue": "Venue",
        "kind": "Kind",
        "type_computational": "computational / model",
        "type_experimental": "experimental",
        "type_method": "method or tool",
        "type_resource": "resource or dataset",
        "type_theory": "theory or review",
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
        "problem": "问题：要解决什么，为什么难",
        "approach": "思路：凭什么这么做能解决",
        "pipeline": "具体怎么做的：逐步拆解",
        "mechanism": "真正起作用的机制",
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
        "no_background": "未提供研究背景，此节留空，不做编造。",
        "banner_lead": "⚠️ 仅有摘要。",
        "banner_body": "没拿到全文，下面没有任何一句建立在论文实际的图表和方法上。"
        "这是摘要，不是精读。",
        "authors": "作者",
        "venue": "发表于",
        "kind": "论文类型",
        "type_computational": "计算 / 模型",
        "type_experimental": "实验",
        "type_method": "方法或工具",
        "type_resource": "资源或数据集",
        "type_theory": "理论或综述",
        "decision_follow-up": "值得跟进",
        "decision_watch": "值得观望",
        "decision_skip": "可以跳过",
        "confidence_high": "高",
        "confidence_medium": "中",
        "confidence_low": "低",
    },
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _as_list(value: Any) -> list[str]:
    """Coerce a rendered list field without exploding a string into characters.

    ``list("watch for a release")`` is what the renderer used to do with a
    string written where a list belongs — twenty-odd items, one character
    each. Validation now rejects that shape; this keeps ``--force`` from
    turning the same mistake into a corrupted document.
    """
    if _blank(value):
        return []
    if not isinstance(value, list):
        return [_text(value)]
    return [_text(item) for item in value if not _blank(item)]


def paragraphs(value: Any) -> list[str]:
    """Split a field into paragraphs on blank lines.

    Long fields are written as several paragraphs. Each has to reach a
    renderer as its own block: a Word paragraph cannot contain a newline, so
    handing over the raw string collapses the structure into one wall of text.
    """
    text = _text(value)
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def build_blocks(note: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a note into a flat list of typed blocks.

    This is the renderer-agnostic form, and the reason it exists is that the
    Word and slide renderers are not written in Python. Handing them Markdown
    would mean parsing a table back out of pipe characters, and handing them
    the raw note would mean reimplementing the heading translations in every
    language they are written in. Blocks carry the resolved labels, so a
    renderer only has to know the eleven shapes in ``BLOCK_TYPES``.

    Every value is coerced rather than trusted, because ``--force`` routes a
    note validation has already rejected through here: a malformed section has
    to render as visibly wrong, not as a traceback.
    """
    lang = note.get("language") or "en"
    s = STRINGS.get(lang, STRINGS["en"]) if isinstance(lang, str) else STRINGS["en"]
    paper = _mapping(note.get("paper"))

    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": s["title"].format(title=_text(paper.get("title")))}
    ]
    if paper.get("fulltext") == "abstract-only":
        blocks.append({"type": "banner", "lead": s["banner_lead"], "text": s["banner_body"]})

    blocks.append({"type": "h1", "text": s["paper"]})
    fields: list[dict[str, str]] = []
    authors = _as_list(paper.get("authors"))
    if authors:
        shown = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        fields.append({"label": s["authors"], "value": shown})
    kind = paper.get("type")
    if kind:
        fields.append({"label": s["kind"], "value": s.get(f"type_{kind}", str(kind))})
    for label, key in ((s["venue"], "venue"), ("DOI", "doi"), ("URL", "url")):
        if paper.get(key):
            fields.append({"label": label, "value": str(paper[key])})
    if fields:
        blocks.append({"type": "fields", "items": fields})

    understanding = _mapping(note.get("understanding"))
    blocks.append({"type": "h1", "text": s["part1"]})
    for field in UNDERSTANDING_FIELDS:
        blocks.append({"type": "h2", "text": s[field]})
        # A long field is written as several paragraphs separated by a blank
        # line. They have to become separate blocks: Word has no newline
        # inside a paragraph, so a renderer handed the raw string produces one
        # run-on wall of text.
        blocks += [{"type": "p", "text": para} for para in paragraphs(understanding.get(field))]

    blocks += _assessment_blocks(_mapping(note.get("assessment")), s)
    blocks += _relevance_blocks(_mapping(note.get("relevance")), s)
    return blocks


def _assessment_blocks(assessment: dict[str, Any], s: dict[str, str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "h1", "text": s["part2"]},
        {"type": "h2", "text": s["claims"]},
    ]

    rows = []
    raw_claims = assessment.get("claims")
    for entry in raw_claims if isinstance(raw_claims, list) else []:
        # A claim written as a bare string is rejected by validation; under
        # --force it still has to reach the table, as its own row.
        claim = entry if isinstance(entry, dict) else {"claim": _text(entry)}
        confidence = _text(claim.get("confidence"))
        rows.append(
            [
                _text(claim.get("claim"), "—"),
                _text(claim.get("evidence"), s["no_evidence"]),
                s.get(f"confidence_{confidence}", confidence),
                _text(claim.get("issue"), "—"),
            ]
        )
    blocks.append(
        {
            "type": "table",
            "header": [s["col_claim"], s["col_evidence"], s["col_confidence"], s["col_issue"]],
            "rows": rows,
        }
    )

    limitations = _mapping(assessment.get("limitations"))
    blocks.append({"type": "h2", "text": s["limitations"]})
    for label, key in ((s["acknowledged"], "acknowledged"), (s["unstated"], "unstated")):
        blocks.append({"type": "label", "text": label})
        blocks.append({"type": "bullets", "items": _as_list(limitations.get(key)) or ["—"]})

    verdict = _mapping(assessment.get("verdict"))
    decision = _text(verdict.get("decision"))
    blocks.append({"type": "h2", "text": s["verdict"]})
    blocks.append(
        {
            "type": "p",
            "lead": s.get(f"decision_{decision}", decision),
            "text": _text(verdict.get("reasoning")),
        }
    )
    if verdict.get("cost"):
        # A labelled paragraph, not a one-item ``fields`` list — a lone bullet
        # under the verdict reads as a list that lost its other entries.
        blocks.append({"type": "p", "lead": s["cost"], "text": _text(verdict["cost"])})
    next_steps = _as_list(verdict.get("next_steps"))
    if next_steps:
        blocks.append({"type": "label", "text": s["next_steps"]})
        blocks.append({"type": "numbered", "items": next_steps})
    return blocks


def _relevance_blocks(relevance: dict[str, Any], s: dict[str, str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "h1", "text": s["relevance"]}]
    if relevance.get("status") == "written":
        blocks.append({"type": "p", "text": _text(relevance.get("text"))})
    else:
        blocks.append({"type": "note", "text": s["no_background"]})
    return blocks


def _md_cell(value: str) -> str:
    """Escape a value for a Markdown table cell.

    Only Markdown needs this. A Word table cell holds the text verbatim, which
    is why escaping happens here rather than in ``build_blocks``.
    """
    return value.replace("|", "\\|").replace("\n", " ")


#: Markdown heading level per block type. ``title`` is ``#`` so the document
#: has exactly one top-level heading.
_MD_HEADINGS = {"title": "#", "h1": "##", "h2": "###"}


def render_markdown(note: dict[str, Any]) -> str:
    """The always-available rendering. Word and slides go through blocks."""
    lines: list[str] = []
    for block in build_blocks(note):
        kind = block["type"]
        if kind in _MD_HEADINGS:
            lines += [f"{_MD_HEADINGS[kind]} {block['text']}", ""]
        elif kind == "banner":
            lines += [f"> **{block['lead']}** {block['text']}", ""]
        elif kind == "note":
            lines += [f"_{block['text']}_", ""]
        elif kind == "label":
            lines += [f"**{block['text']}**", ""]
        elif kind == "p":
            lead = block.get("lead")
            lines += [f"**{lead}** — {block['text']}" if lead else block["text"], ""]
        elif kind == "fields":
            lines += [f"- **{f['label']}**: {f['value']}" for f in block["items"]]
            lines.append("")
        elif kind == "bullets":
            lines += [f"- {item}" for item in block["items"]]
            lines.append("")
        elif kind == "numbered":
            lines += [f"{i}. {item}" for i, item in enumerate(block["items"], 1)]
            lines.append("")
        elif kind == "table":
            lines.append("| " + " | ".join(block["header"]) + " |")
            lines.append("|" + "---|" * len(block["header"]))
            for row in block["rows"]:
                lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
            lines.append("")
        else:
            # Without this, a block type added to build_blocks and forgotten
            # here vanishes from the Markdown with no error, no warning and a
            # zero exit — the two renderings silently disagree about what the
            # document contains. The external Word renderer is exposed to the
            # same drift; here at least it is loud.
            raise NoteError(f"unhandled block type {kind!r} — render_markdown is out of step")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError: a note saved as
        # UTF-16 or latin-1 used to come out as a raw traceback.
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

    render_cmd = sub.add_parser("render", help="render a note to Markdown or to blocks")
    render_cmd.add_argument("note", type=Path)
    render_cmd.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    render_cmd.add_argument(
        "--format",
        choices=("md", "blocks"),
        default="md",
        help="md (default) or blocks — typed JSON for a non-Python renderer such "
        "as the docx skill, with headings already in the note's language",
    )
    render_cmd.add_argument(
        "--force",
        action="store_true",
        help="render even if validation fails; every error is still reported on stderr",
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
    if errors:
        # --force overrides the errors; it does not get to hide them. A note
        # rendered past a rejected claim or an empty section reads as finished,
        # and the empty section is a heading the reader skims straight past.
        for error in errors:
            print(f"overridden: {error}", file=sys.stderr)
        print(
            f"--force: rendered with {len(errors)} unfixed error(s) listed above",
            file=sys.stderr,
        )

    try:
        if args.format == "blocks":
            rendered = json.dumps(build_blocks(note), ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = render_markdown(note)
    except NoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

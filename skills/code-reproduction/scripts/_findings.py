"""The record every check produces, and the vocabulary it draws on.

A finding is one observation about a repository. It may only exist if it can
name the file that produced it: `Finding` refuses to be built without at least
one `Evidence`. That is not ceremony. The whole point of this skill is to tell
someone their afternoon is about to be wasted, and "the weights look gated" is
worth nothing next to "``env/download_startup.sh:412`` fetches from
``huggingface.co`` and ``.env_example:24`` wants ``HF_TOKEN``". A reader has to
be able to go and look.

The other half of a finding is `requires`: the machine-checkable form of what
the repository is asking for. `survey.py` fills it from the repository, never
from the host; `gate.py` is the only thing that compares it against a host.
Keeping those apart is what lets a survey be run somewhere other than the
machine that will do the work — the common case, since the machine that will
do the work is usually the one you are deciding whether to book.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Where a finding lives. Roughly "which gate would this close".
LAYERS = ("license", "weights", "data", "env", "hardware", "handoff")

# How bad it is on its own terms, before any host is considered. A
# non-commercial weights licence is `blocking` on every host in the world; a
# 40 GB VRAM requirement is only blocking once you know what card you have,
# so it arrives as `note` carrying a `requires`, and `gate.py` decides.
SEVERITIES = ("blocking", "degraded", "note")

# What a finding gates. Inference is the default target because it is the one
# people actually attempt: released weights, released data, a published
# number. Training is opt-in and mostly answers "how far out of reach is it".
TARGETS = ("inference", "training")

# The vocabulary of `Finding.requires`. gate.py raises on anything outside
# this set rather than skipping it — an unrecognised requirement silently
# dropped is exactly the "looks clear, isn't" failure this skill exists to
# prevent.
REQUIREMENT_KEYS = (
    "gpu",  # bool — needs a CUDA device at all
    "vram_gb",  # number — per-device video memory
    "disk_gb",  # number — free disk for weights and outputs
    "python",  # str — a PEP 440 specifier, e.g. ">=3.12"
    "glibc_min",  # str — e.g. "2.35"
    "cuda_min",  # str — e.g. "12.4"
    "os",  # str — "linux" | "darwin" | "windows"
    "env_vars",  # list[str] — credentials that must be set
    "network_hosts",  # list[str] — hosts that must be reachable
)

_QUOTE_LIMIT = 200


class FindingError(Exception):
    """A finding was built out of a vocabulary it does not have."""


@dataclass(frozen=True)
class Evidence:
    """Where a finding came from. `path` is relative to the repository root."""

    path: str
    quote: str
    line: int | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise FindingError("evidence needs a path — a finding with no source is a guess")

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "quote": _clip(self.quote)}

    @classmethod
    def from_dict(cls, raw: Any) -> Evidence:
        raw = raw if isinstance(raw, dict) else {}
        return cls(path=str(raw.get("path", "")), quote=str(raw.get("quote", "")), line=_int(raw))

    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass(frozen=True)
class Finding:
    """One checkable observation about a repository."""

    id: str
    layer: str
    severity: str
    summary: str
    evidence: tuple[Evidence, ...]
    targets: tuple[str, ...] = TARGETS
    detail: str = ""
    requires: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise FindingError(f"{self.id}: unknown layer {self.layer!r}, expected one of {LAYERS}")
        if self.severity not in SEVERITIES:
            raise FindingError(f"{self.id}: unknown severity {self.severity!r}")
        unknown_targets = [t for t in self.targets if t not in TARGETS]
        if unknown_targets:
            raise FindingError(f"{self.id}: unknown target(s) {unknown_targets}")
        if not self.targets:
            raise FindingError(f"{self.id}: a finding that gates nothing should not exist")
        if not self.evidence:
            raise FindingError(
                f"{self.id}: no evidence. Every finding names the file it came from, "
                "so the reader can go and check it."
            )
        unknown_requires = [k for k in self.requires if k not in REQUIREMENT_KEYS]
        if unknown_requires:
            raise FindingError(
                f"{self.id}: unknown requirement key(s) {unknown_requires}. "
                f"gate.py can only check {list(REQUIREMENT_KEYS)}; adding a key here "
                "without teaching gate.py about it would drop it silently."
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "layer": self.layer,
            "severity": self.severity,
            "summary": self.summary,
            "targets": list(self.targets),
            "evidence": [e.to_dict() for e in self.evidence],
        }
        if self.detail:
            out["detail"] = self.detail
        if self.requires:
            out["requires"] = dict(self.requires)
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> Finding:
        raw = raw if isinstance(raw, dict) else {}
        evidence = raw.get("evidence")
        requires = raw.get("requires", {})
        if not isinstance(requires, dict):
            # Dropping it and carrying on would take a gate out of the report
            # without anything saying so — the finding would still be listed,
            # and it would be listed as met.
            raise FindingError(
                f"{raw.get('id', '?')}: `requires` must be an object, found "
                f"{type(requires).__name__}"
            )
        return cls(
            id=str(raw.get("id", "")),
            layer=str(raw.get("layer", "")),
            severity=str(raw.get("severity", "")),
            summary=str(raw.get("summary", "")),
            evidence=tuple(
                Evidence.from_dict(e) for e in (evidence if isinstance(evidence, list) else [])
            ),
            targets=tuple(str(t) for t in raw.get("targets", TARGETS)),
            detail=str(raw.get("detail", "")),
            requires=dict(requires),
        )

    def gates(self, target: str) -> bool:
        return target in self.targets


def by_severity(findings: list[Finding]) -> list[Finding]:
    """Worst first, then by layer, then by id — a stable reading order."""
    return sorted(
        findings,
        key=lambda f: (SEVERITIES.index(f.severity), LAYERS.index(f.layer), f.id),
    )


def write_json(payload: dict[str, Any], path: str | None) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path and path != "-":
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return text


def read_json(path: str) -> dict[str, Any]:
    """Read a survey or probe file, and say which one failed when it does."""
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FindingError(f"could not read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise FindingError(f"{path} should hold a JSON object, found {type(loaded).__name__}")
    return loaded


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _QUOTE_LIMIT else text[: _QUOTE_LIMIT - 1] + "…"


def _int(raw: dict[str, Any]) -> int | None:
    value = raw.get("line")
    return value if isinstance(value, int) else None

#!/usr/bin/env python3
"""Decide whether this host can attempt this repository, and say why not.

`survey.py` says what the repository asks for. `probe.py` says what the host
offers. This is the only place the two are compared, and the only place a
verdict is issued.

Four verdicts, and `unknown` is the one that matters:

    blocked    a requirement is stated and this host does not meet it
    unknown    the requirement or the capability could not be determined
    degraded   it will run, with a documented problem you should know about
    ok         stated and met

`unknown` is deliberately worse than `degraded` in the ordering. A stated
40 GB requirement against an unknown card is not a pass, and the failure mode
this whole skill exists to prevent is a report that reads "clear" because a
check quietly found nothing. Anything the survey could not settle arrives here
as an unknown too, so the gaps in the survey are visible in the verdict rather
than absent from it.

Target defaults to inference. Reproducing inference — released weights,
released data, a published number — is what people actually attempt, and its
gates are access gates. Training is opt-in and its gates are mostly different
ones: the dataset that shipped as a list of accession numbers, the cluster the
schedule assumes. Findings that gate only training are excluded from an
inference verdict and counted out loud, so nobody reads `ok` and concludes
they can retrain.

Usage:
    python3 gate.py --survey survey.json --probe probe.json [--target inference]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from _findings import Finding, FindingError, read_json, write_json

SCHEMA = "code-reproduction/gate/1"

# What each input file must announce itself as. Checked rather than assumed:
# the two flags take structurally similar JSON, and getting them the wrong way
# round used to produce the most reassuring output this program can print.
SURVEY_SCHEMA = "code-reproduction/survey/"
PROBE_SCHEMA = "code-reproduction/probe/"

# Worst first. `_worst` relies on this order.
VERDICTS = ("blocked", "unknown", "degraded", "ok")

SEVERITY_VERDICT = {"blocking": "blocked", "degraded": "degraded", "note": "ok"}

_SPEC = re.compile(r"^\s*(~=|==|!=|<=|>=|<|>)?\s*([0-9][0-9.*]*)\s*$")


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """A section of a probe or survey file, or an empty one.

    Probe files are written by a script whose whole contract is that missing
    facts arrive as absent rather than as an exception. Reading them has to
    match: a section that is not there reads as empty, not as a crash.
    """
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _require_schema(payload: dict[str, Any], expected: str, flag: str) -> None:
    """Refuse a file that is not the kind this flag asked for.

    `--survey probe.json --probe survey.json` used to print `Verdict: OK` and
    exit 0: a probe file has no `findings` key, no findings meant nothing to
    gate, and nothing to gate meant pass. Both halves of that are fixed — this
    check, and `_worst` no longer treating an empty comparison as a clean one.
    """
    found = payload.get("schema")
    if isinstance(found, str) and found.startswith(expected):
        return
    other = PROBE_SCHEMA if expected == SURVEY_SCHEMA else SURVEY_SCHEMA
    swapped = isinstance(found, str) and found.startswith(other)
    raise FindingError(
        f"{flag} was handed a file declaring schema {found!r}, expected {expected}*"
        + (" — the --survey and --probe arguments look swapped" if swapped else "")
    )


def _listing(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _worst(verdicts: list[str]) -> str:
    """The worst verdict present. An empty comparison is not a pass.

    Nothing to compare means no requirement was evaluated at all, which is a
    survey that did not run rather than a host that qualified.
    """
    for verdict in VERDICTS:
        if verdict in verdicts:
            return verdict
    return "unknown"


def _version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    parts = re.findall(r"\d+", str(text))
    return tuple(int(p) for p in parts) if parts else None


def _at_least(found: str | None, needed: str) -> str:
    """Compare two dotted versions, padding the shorter with zeros.

    Padding rather than truncating is the conservative direction: CUDA "12"
    against a "12.6" floor comes out blocked, not ok. A version string too
    coarse to answer the question has not answered it.
    """
    have, want = _version(found), _version(needed)
    if have is None or want is None:
        return "unknown"
    width = max(len(have), len(want))
    have += (0,) * (width - len(have))
    want += (0,) * (width - len(want))
    return "ok" if have >= want else "blocked"


def python_satisfies(spec: str, version: str) -> bool | None:
    """Evaluate a PEP 440-ish specifier. None when it cannot be parsed.

    Only the operators that show up in `requires-python` are supported. An
    unrecognised clause returns None rather than True — a specifier this
    cannot read is not a specifier this host has met.
    """
    have = _version(version)
    if have is None:
        return None
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = _SPEC.match(clause)
        if not match:
            return None
        operator, raw = match.group(1) or "==", match.group(2)
        wildcard = raw.endswith(".*")
        want = _version(raw)
        if want is None:
            return None
        left = have[: len(want)] if (wildcard or operator in {"==", "!="}) else have
        right = want
        if operator == ">=" and not (left >= right):
            return False
        if operator == ">" and not (left > right):
            return False
        if operator == "<=" and not (left <= right):
            return False
        if operator == "<" and not (left < right):
            return False
        if operator == "==" and left != right:
            return False
        if operator == "!=" and left == right:
            return False
        if operator == "~=" and (
            len(want) < 2
            or not (have >= want and have[: len(want) - 1] == want[: len(want) - 1])
        ):
            return False
    return True


def evaluate(key: str, needed: Any, probe: dict[str, Any]) -> dict[str, Any]:
    """One requirement against the host. Always returns a verdict and a reason."""
    gpu = _mapping(probe, "gpu")
    host = _mapping(probe, "platform")

    if key == "gpu":
        if not needed:
            return _gate(key, needed, None, "ok", "no GPU required")
        available = gpu.get("available")
        if available is None:
            return _gate(
                key, needed, None, "unknown", "the host probe did not report a GPU section"
            )
        if available:
            return _gate(key, needed, True, "ok", "a CUDA device is present")
        # "there is no GPU" and "nothing here could ask" are different claims,
        # and only the first is a fact about this host. Telling someone their
        # machine is unsuitable when `nvidia-smi` was merely off the PATH sends
        # them to book different hardware they already have.
        verdict = "blocked" if gpu.get("determined") is True else "unknown"
        return _gate(key, needed, False, verdict, gpu.get("why", "no CUDA device visible"))

    if key == "vram_gb":
        have = gpu.get("vram_gb")
        if have is None:
            return _gate(key, needed, None, "unknown", "no VRAM figure from the host")
        return (
            _gate(key, needed, have, "ok", f"{have} GB available, {needed} GB asked for")
            if have >= needed
            else _gate(key, needed, have, "blocked", f"{have} GB available, {needed} GB asked for")
        )

    if key == "disk_gb":
        have = (probe.get("disk") or {}).get("free_gb")
        if have is None:
            return _gate(key, needed, None, "unknown", "free space could not be read")
        return (
            _gate(key, needed, have, "ok", f"{have} GB free, {needed} GB stated")
            if have >= needed
            else _gate(key, needed, have, "blocked", f"{have} GB free, {needed} GB stated")
        )

    if key == "python":
        have = (probe.get("python") or {}).get("short")
        verdict = python_satisfies(str(needed), str(have)) if have else None
        if verdict is None:
            return _gate(
                key, needed, have, "unknown", f"could not compare {have!r} against {needed!r}"
            )
        return _gate(
            key,
            needed,
            have,
            "ok" if verdict else "blocked",
            f"the interpreter that ran the probe is Python {have}, repository wants {needed}",
        )

    if key == "glibc_min":
        have = host.get("glibc")
        if not have:
            return _gate(
                key, needed, have, "unknown", "no glibc version reported (not a Linux host?)"
            )
        verdict = _at_least(have, str(needed))
        return _gate(key, needed, have, verdict, f"glibc {have} against a {needed} floor")

    if key == "cuda_min":
        have = gpu.get("cuda")
        if not have:
            return _gate(key, needed, have, "unknown", "no CUDA runtime version reported")
        verdict = _at_least(have, str(needed))
        return _gate(
            key, needed, have, verdict, f"driver reports CUDA {have}, build wants {needed}"
        )

    if key == "os":
        have = host.get("os")
        if not have:
            return _gate(key, needed, have, "unknown", "host OS not reported")
        return _gate(
            key,
            needed,
            have,
            "ok" if have == needed else "blocked",
            f"host is {have}, repository targets {needed}",
        )

    if key == "env_vars":
        table = _mapping(probe, "env_vars")
        missing = [name for name in needed if not _mapping(table, name).get("set")]
        unchecked = [name for name in needed if name not in table]
        if unchecked:
            return _gate(
                key, needed, None, "unknown", f"not checked here: {', '.join(unchecked)}"
            )
        if missing:
            return _gate(key, needed, missing, "blocked", f"not set: {', '.join(missing)}")
        return _gate(key, needed, [], "ok", "all present")

    if key == "network_hosts":
        table = _mapping(probe, "reachability")
        unchecked = [h for h in needed if h not in table]
        if unchecked:
            return _gate(
                key, needed, None, "unknown", f"reachability not tested: {', '.join(unchecked)}"
            )
        unreachable = [h for h in needed if not _mapping(table, h).get("reachable")]
        if unreachable:
            return _gate(
                key, needed, unreachable, "blocked", f"unreachable: {', '.join(unreachable)}"
            )
        return _gate(key, needed, [], "ok", "all reachable")

    raise FindingError(
        f"gate.py has no rule for requirement {key!r}. _findings.REQUIREMENT_KEYS lists it, "
        "so it was added there without being taught here — which would let it pass silently."
    )


def _gate(key: str, needed: Any, found: Any, verdict: str, why: str) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise FindingError(
            f"{key}: {verdict!r} is not a verdict. `_worst` scans for the ones it knows and "
            f"ignores the rest, so a typo in one of these literals would read as a pass. "
            f"Expected one of {VERDICTS}."
        )
    return {"requirement": key, "needed": needed, "found": found, "verdict": verdict, "why": why}


def assess(
    survey: dict[str, Any], probe: dict[str, Any], target: str = "inference"
) -> dict[str, Any]:
    _require_schema(survey, SURVEY_SCHEMA, "--survey")
    _require_schema(probe, PROBE_SCHEMA, "--probe")

    findings = [Finding.from_dict(raw) for raw in _listing(survey, "findings")]
    applicable = [f for f in findings if f.gates(target)]
    deferred = [f for f in findings if not f.gates(target)]

    rows: list[dict[str, Any]] = []
    for finding in applicable:
        gates = [evaluate(key, value, probe) for key, value in sorted(finding.requires.items())]
        # The host result and the finding's own severity, worst wins. A login
        # wall you can reach is still a login wall: passing the reachability
        # gate does not turn a `degraded` finding into a clean one, and a
        # report that said otherwise would be reassuring about the wrong half.
        verdict = _worst([g["verdict"] for g in gates] + [SEVERITY_VERDICT[finding.severity]])
        rows.append(
            {
                "id": finding.id,
                "layer": finding.layer,
                "severity": finding.severity,
                "summary": finding.summary,
                "detail": finding.detail,
                "evidence": [e.to_dict() for e in finding.evidence],
                "gates": gates,
                "verdict": verdict,
            }
        )

    # A malformed entry is still an entry: the survey recorded that something
    # stayed open. Dropping it for being the wrong shape would remove an
    # `unknown` from the verdict, which is the one direction that must never
    # happen quietly.
    unresolved = [
        {"check": item.get("check", "?"), "why": item.get("why", "")}
        if isinstance(item, dict)
        else {"check": "?", "why": f"unreadable entry in the survey's inconclusive list: {item!r}"}
        for item in _listing(survey, "inconclusive")
    ]

    verdicts = [row["verdict"] for row in rows] + (["unknown"] if unresolved else [])
    if not verdicts:
        # Nothing gates this target — which is a real answer when the survey
        # saw things and deferred them all to the other one, and a broken
        # survey when it saw nothing at all. Those are different reports and
        # the difference is stated here rather than left to a default.
        verdicts = ["ok" if findings else "unknown"]
    overall = _worst(verdicts)

    return {
        "schema": SCHEMA,
        "target": target,
        "verdict": overall,
        "repo": survey.get("repo", {}),
        "host": _host_summary(probe),
        "counts": {v: sum(1 for row in rows if row["verdict"] == v) for v in VERDICTS},
        "findings": sorted(rows, key=lambda row: (VERDICTS.index(row["verdict"]), row["layer"])),
        "unresolved": unresolved,
        "deferred": [
            {"id": f.id, "summary": f.summary, "targets": list(f.targets)} for f in deferred
        ],
    }


def _host_summary(probe: dict[str, Any]) -> dict[str, Any]:
    host = probe.get("platform") or {}
    gpu = probe.get("gpu") or {}
    return {
        "os": host.get("os"),
        "machine": host.get("machine"),
        "glibc": host.get("glibc"),
        "python": (probe.get("python") or {}).get("short"),
        "gpu": (
            ", ".join(f"{d.get('name')} ({d.get('vram_gb')} GB)" for d in gpu.get("devices", []))
            if gpu.get("available")
            else None
        ),
        "cuda": gpu.get("cuda"),
        "free_gb": (probe.get("disk") or {}).get("free_gb"),
    }


HEADLINE = {
    "blocked": "Do not start. At least one requirement is stated and unmet.",
    "unknown": "Do not start yet. Something could not be determined, and an unknown is not a pass.",
    "degraded": "You can start, with known problems ahead. Read these first.",
    "ok": "Every stated requirement this survey found is met on this host.",
}


def render_markdown(report: dict[str, Any]) -> str:
    repo = report.get("repo", {})
    host = report.get("host", {})
    out = [
        f"# Reproduction gate — {repo.get('name', 'repository')}",
        "",
        f"**Verdict: {report['verdict'].upper()}** ({report['target']})  ",
        HEADLINE[report["verdict"]],
        "",
        "| | |",
        "|---|---|",  # headerless on purpose: it is a fact sheet, not a comparison
        f"| Repository | `{repo.get('root', '?')}` |",
        f"| Commit | `{(repo.get('git') or {}).get('commit') or 'unknown'}` |",
        f"| Host | {host.get('os')} / {host.get('machine')}"
        + (f" / glibc {host['glibc']}" if host.get("glibc") else "")
        + f" / Python {host.get('python')} |",
        f"| GPU | {host.get('gpu') or 'none detected'}"
        + (f" / CUDA {host['cuda']}" if host.get("cuda") else "")
        + " |",
        f"| Free disk | {host.get('free_gb')} GB |",
        "",
    ]

    guidance = [row for row in report["findings"] if row["id"] == "handoff.repo-ships-guidance"]
    if guidance:
        where = ", ".join(f"`{e['path']}`" for e in guidance[0]["evidence"][:4])
        out += [
            "> **Read the repository's own instructions first.** It ships them, and whoever",
            "> wrote them knows this pipeline better than any general survey does. What",
            "> follows covers what such files usually leave out — licence layers, credentials,",
            "> and whether this particular host qualifies at all.",
            ">",
            f"> {where}",
            "",
        ]

    for verdict, heading in (
        ("blocked", "## Blocked"),
        ("unknown", "## Could not be determined"),
        ("degraded", "## Known problems ahead"),
    ):
        rows = [row for row in report["findings"] if row["verdict"] == verdict]
        if not rows:
            continue
        out += [heading, ""]
        for row in rows:
            out += [f"### {row['summary']}", ""]
            unmet = [
                f"- `{gate['requirement']}` — {gate['why']}"
                for gate in row["gates"]
                if gate["verdict"] != "ok"
            ]
            if unmet:
                out += unmet + [""]
            if row["detail"]:
                out += [row["detail"], ""]
            sources = ", ".join(
                f"`{e['path']}:{e['line']}`" if e.get("line") else f"`{e['path']}`"
                for e in row["evidence"][:4]
            )
            out += [f"Evidence: {sources}", ""]

    if report["unresolved"]:
        out += ["## Open questions from the survey", ""]
        out += [f"- **{item['check']}** — {item['why']}" for item in report["unresolved"]]
        out.append("")

    # Two different kinds of "fine", and collapsing them misleads. A stated
    # requirement this host meets is a pass. A remark with nothing to check
    # against — no CI, no tests — was never a requirement, and filing it under
    # "met" reads as though something was verified.
    met = [row for row in report["findings"] if row["verdict"] == "ok" and row["gates"]]
    if met:
        out += ["## Checked and met", ""]
        out += [
            f"- {row['summary']} — {'; '.join(gate['why'] for gate in row['gates'])}" for row in met
        ]
        out.append("")

    noted = [row for row in report["findings"] if row["verdict"] == "ok" and not row["gates"]]
    if noted:
        out += [
            "## Worth knowing",
            "",
            "Nothing here gates the run; all of it shapes the estimate.",
            "",
        ]
        out += [f"- {row['summary']}" for row in noted]
        out.append("")

    if report["deferred"]:
        other = "training" if report["target"] == "inference" else "inference"
        out += [
            f"## Not evaluated ({len(report['deferred'])} finding(s) gate {other} only)",
            "",
            f"This verdict covers **{report['target']}** and says nothing about {other}. "
            f"Re-run with `--target {other}` to see them.",
            "",
        ]
        out += [f"- {item['summary']}" for item in report["deferred"]]
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--survey", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--target", choices=("inference", "training"), default="inference")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out", help="write the report here as well as to stdout")
    args = parser.parse_args(argv)

    try:
        report = assess(read_json(args.survey), read_json(args.probe), args.target)
    except FindingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        sys.stdout.write(write_json(report, args.out))
    else:
        text = render_markdown(report)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
        sys.stdout.write(text)

    return 1 if report["verdict"] in {"blocked", "unknown"} else 0


if __name__ == "__main__":
    raise SystemExit(main())

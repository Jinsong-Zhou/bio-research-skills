#!/usr/bin/env python3
"""Record what this host actually offers. Never guess, never raise.

Every probe degrades to `null` or `{"available": false}` rather than failing,
because a probe that dies takes the whole report with it, and the reason you
are running this is usually that something about the machine is unusual.

Two rules worth stating outright:

  * A value that could not be determined is recorded as `null`, and `gate.py`
    turns that into `unknown` — never into "fine". A missing `nvidia-smi` is
    not evidence of a missing GPU; it is evidence of a missing `nvidia-smi`.
  * Credentials are reported as present or absent and nothing else. The value
    of `HF_TOKEN` never enters this file, and this file is meant to be
    committed next to a reproduction log.

`--from-survey` reads a `survey.json` and probes exactly what that repository
asked for: its credentials, its hosts. The two scripts stay decoupled — they
share a file, not an import.

Usage:
    python3 probe.py [--from-survey survey.json] [--disk PATH] [--reach HOST ...]

Acknowledgement: the shape of this probe — one JSON snapshot per host, every
field degrading rather than failing — follows `.claude/skills/_shared/scripts/
preflight.sh` in NVIDIA-BioNeMo/Proteina-Complexa (Apache-2.0). No code is
copied; it is a good pattern and it deserves the credit.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "code-reproduction/probe/1"
TOOLS = ("git", "docker", "uv", "conda", "mamba", "nvcc", "nvidia-smi", "singularity", "apptainer")
NVIDIA_QUERY = "name,memory.total,driver_version"
CUDA_FROM_SMI = re.compile(r"CUDA Version:\s*([0-9.]+)")
CONNECT_TIMEOUT = 4.0


def _run(command: list[str], timeout: float = 15.0) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def probe_platform() -> dict[str, Any]:
    system = platform.system().lower()
    libc_name, libc_version = ("", "")
    if system == "linux":
        try:
            libc_name, libc_version = platform.libc_ver()
        except OSError:
            libc_name, libc_version = ("", "")
    return {
        "os": system or None,
        "release": platform.release() or None,
        "machine": platform.machine() or None,
        "glibc": libc_version or None,
        "libc": libc_name or None,
        "apple_silicon": system == "darwin" and platform.machine() == "arm64",
    }


def probe_python() -> dict[str, Any]:
    info = sys.version_info
    return {
        "version": f"{info.major}.{info.minor}.{info.micro}",
        "short": f"{info.major}.{info.minor}",
        "executable": sys.executable,
        "note": "the interpreter that ran this probe, which need not be the one that "
        "will run the repository",
    }


def probe_gpu() -> dict[str, Any]:
    """CUDA devices via `nvidia-smi`, with Apple's unified memory noted separately."""
    if shutil.which("nvidia-smi") is None:
        return {
            "available": False,
            "why": "nvidia-smi is not on PATH — no CUDA device is visible from here",
            "devices": [],
            "vram_gb": None,
            "cuda": None,
        }

    listing = _run(["nvidia-smi", f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits"])
    if not listing:
        return {
            "available": False,
            "why": "nvidia-smi is installed but returned nothing — driver or permission problem",
            "devices": [],
            "vram_gb": None,
            "cuda": None,
        }

    devices: list[dict[str, Any]] = []
    for row in listing.splitlines():
        parts = [cell.strip() for cell in row.split(",")]
        if len(parts) < 3:
            continue
        try:
            vram_gb = round(float(parts[1]) / 1024, 1)
        except ValueError:
            vram_gb = None
        devices.append({"name": parts[0], "vram_gb": vram_gb, "driver": parts[2]})

    banner = _run(["nvidia-smi"]) or ""
    cuda_match = CUDA_FROM_SMI.search(banner)
    sizes = [d["vram_gb"] for d in devices if d["vram_gb"] is not None]
    return {
        "available": bool(devices),
        "devices": devices,
        "count": len(devices),
        "vram_gb": min(sizes) if sizes else None,
        "cuda": cuda_match.group(1) if cuda_match else None,
    }


def probe_disk(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {"path": str(target), "free_gb": None, "why": str(exc)}
    return {"path": str(target), "free_gb": round(usage.free / 1024**3, 1)}


def probe_tools() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for tool in TOOLS:
        location = shutil.which(tool)
        found[tool] = {"present": location is not None, "path": location}
    return found


def probe_env_vars(names: list[str]) -> dict[str, Any]:
    """Presence only. The values are secrets and this file gets shared."""
    return {name: {"set": bool(os.environ.get(name, "").strip())} for name in sorted(set(names))}


def probe_reachability(hosts: list[str]) -> dict[str, Any]:
    """A TCP connect on 443. Not a fetch — no credential is offered, nothing is downloaded."""
    results: dict[str, Any] = {}
    for host in sorted(set(hosts)):
        clean = host.strip().rstrip("/")
        if not clean:
            continue
        try:
            with socket.create_connection((clean, 443), timeout=CONNECT_TIMEOUT):
                results[clean] = {"reachable": True}
        except OSError as exc:
            results[clean] = {"reachable": False, "why": str(exc)}
    return results


def wanted_from_survey(path: str) -> tuple[list[str], list[str]]:
    """The credentials and hosts a survey said the repository needs."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: could not read {path}: {exc}") from exc

    env_vars: list[str] = []
    hosts: list[str] = []
    findings = payload.get("findings") if isinstance(payload, dict) else None
    for raw in findings if isinstance(findings, list) else []:
        requires = raw.get("requires") if isinstance(raw, dict) else None
        if not isinstance(requires, dict):
            continue
        env_vars += [str(v) for v in requires.get("env_vars", []) if isinstance(v, str)]
        hosts += [str(v) for v in requires.get("network_hosts", []) if isinstance(v, str)]
    return env_vars, hosts


def probe_host(
    disk_path: str = ".",
    env_vars: list[str] | None = None,
    hosts: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "platform": probe_platform(),
        "python": probe_python(),
        "gpu": probe_gpu(),
        "disk": probe_disk(disk_path),
        "tools": probe_tools(),
        "env_vars": probe_env_vars(env_vars or []),
    }
    payload["reachability"] = probe_reachability(hosts) if hosts else {}
    return payload


def render_text(payload: dict[str, Any]) -> str:
    host = payload["platform"]
    gpu = payload["gpu"]
    lines = [
        f"os        {host['os']} {host['release']} ({host['machine']})"
        + (f", glibc {host['glibc']}" if host["glibc"] else ""),
        f"python    {payload['python']['version']}",
    ]
    if gpu["available"]:
        names = ", ".join(f"{d['name']} {d['vram_gb']}GB" for d in gpu["devices"])
        lines.append(f"gpu       {gpu['count']}x {names}, CUDA {gpu['cuda'] or 'unknown'}")
    else:
        lines.append(f"gpu       none — {gpu.get('why', 'not detected')}")
    lines.append(f"disk      {payload['disk']['free_gb']} GB free at {payload['disk']['path']}")
    present = [name for name, info in payload["tools"].items() if info["present"]]
    lines.append(f"tools     {', '.join(present) if present else 'none of the usual ones'}")
    for name, info in payload["env_vars"].items():
        lines.append(f"env       {name}: {'set' if info['set'] else 'NOT SET'}")
    for name, info in payload["reachability"].items():
        lines.append(f"reach     {name}: {'ok' if info['reachable'] else 'unreachable'}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--from-survey", help="take credentials and hosts to check from a survey.json"
    )
    parser.add_argument("--disk", default=".", help="where the weights and outputs will land")
    parser.add_argument(
        "--env-var", action="append", default=[], help="also check this variable is set"
    )
    parser.add_argument(
        "--reach",
        action="append",
        default=[],
        help="also check this host answers on 443 (opt-in: this is the only network access here)",
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--out", help="write JSON here as well as to stdout")
    args = parser.parse_args(argv)

    env_vars = list(args.env_var)
    hosts = list(args.reach)
    if args.from_survey:
        from_survey_env, from_survey_hosts = wanted_from_survey(args.from_survey)
        env_vars += from_survey_env
        hosts += from_survey_hosts

    payload = probe_host(disk_path=args.disk, env_vars=env_vars, hosts=hosts)

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.write(render_text(payload) if args.format == "text" else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

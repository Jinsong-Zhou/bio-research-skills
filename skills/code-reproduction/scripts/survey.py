#!/usr/bin/env python3
"""Read a research repository and write down what it will demand before it runs.

Six groups of checks, each producing `Finding`s that name the file they came
from:

    license   the four layers — code, weights, data, third-party — read
              separately, because a repository's root LICENSE routinely
              governs none of them
    weights   where the checkpoints come from, what credential the fetch
              wants, how many gigabytes land on disk
    data      whether the "dataset" is data or a list of accession numbers
    env       the Python and CUDA constraints, and the install failures the
              build script swallows
    hardware  the GPU the docs assume you have
    handoff   whether the repository already ships agent instructions, CI or
              tests — that is, whether anyone upstream is checking it still
              builds

This script never decides whether a host qualifies. It records what the
repository asks for in `Finding.requires`; `probe.py` records what a host
offers; `gate.py` is the only place the two meet. That split is what lets you
survey a repository from a laptop and gate it against a cluster you have not
booked yet.

Usage:
    python3 survey.py <repo-dir> [--format json|text] [--out survey.json]
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from _findings import Evidence, Finding, FindingError, by_severity, write_json

SCHEMA = "code-reproduction/survey/1"

SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env.bak",
        "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox",
        "dist", "build", ".eggs", "site-packages", ".idea", ".vscode",
    }
)

# Directories whose contents belong to somebody else. Their tests are not this
# repository's tests, and their licences are third-party by definition.
VENDORED_DIRS = (
    "community_models", "third_party", "thirdparty", "vendor", "external", "submodules",
)

BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".bz2",
        ".xz", ".7z", ".whl", ".so", ".dylib", ".dll", ".bin", ".ckpt", ".pt",
        ".pth", ".safetensors", ".npz", ".npy", ".h5", ".hdf5", ".pkl", ".parquet",
        ".ico", ".svg", ".woff", ".woff2", ".ttf", ".mp4", ".webm", ".cif", ".pdb",
    }
)

DOC_SUFFIXES = frozenset({".md", ".rst", ".txt"})
SHELL_SUFFIXES = frozenset({".sh", ".bash", ".zsh"})
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 20_000

# --------------------------------------------------------------------------
# license
# --------------------------------------------------------------------------

LICENSE_NAME = re.compile(r"^(licen[sc]e|copying|notice)", re.IGNORECASE)
LICENSE_DIR = re.compile(r"(^|/)licen[sc]es?/", re.IGNORECASE)

# Ordered: the first match wins, so narrower variants come before the family
# they belong to (AGPL before GPL, CC-BY-NC before CC-BY).
LICENSE_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AGPL-3.0", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.IGNORECASE)),
    ("LGPL", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE", re.IGNORECASE)),
    ("GPL", re.compile(r"GNU GENERAL PUBLIC LICENSE", re.IGNORECASE)),
    ("Apache-2.0", re.compile(r"Apache License\s*,?\s*\n?\s*Version 2\.0", re.IGNORECASE)),
    ("MIT", re.compile(r"Permission is hereby granted, free of charge", re.IGNORECASE)),
    ("BSD", re.compile(r"Redistributions of source code must retain", re.IGNORECASE)),
    ("MPL-2.0", re.compile(r"Mozilla Public License", re.IGNORECASE)),
    ("CC-BY-NC", re.compile(r"Attribution-?\s?NonCommercial", re.IGNORECASE)),
    ("CC-BY-SA", re.compile(r"Attribution-?\s?ShareAlike", re.IGNORECASE)),
    ("CC-BY-4.0", re.compile(r"Creative Commons Attribution", re.IGNORECASE)),
    ("CC0", re.compile(r"CC0 1\.0|Creative Commons Zero", re.IGNORECASE)),
    ("NVIDIA Open Model License", re.compile(r"NVIDIA Open Model License", re.IGNORECASE)),
    ("NVIDIA (other)", re.compile(r"NVIDIA\s+\w+\s+License Agreement", re.IGNORECASE)),
    ("Llama Community License", re.compile(r"LLAMA\s*[\d.]*\s*COMMUNITY LICENSE", re.IGNORECASE)),
    ("OpenRAIL", re.compile(r"Responsible AI License|\bOpenRAIL\b", re.IGNORECASE)),
    ("Beer-ware", re.compile(r"BEER-?WARE", re.IGNORECASE)),
    ("Unlicense", re.compile(r"This is free and unencumbered software", re.IGNORECASE)),
)

# Deliberately narrow. "commercial" on its own is useless — the NVIDIA Open
# Model License says "Models are commercially usable", and a scan for the bare
# word flags it as restricted.
NONCOMMERCIAL = re.compile(
    r"non-?commercial"
    r"|not (?:be )?(?:used |licensed )?for commercial"
    r"|research (?:and educational )?(?:purposes? |use )?only"
    r"|academic (?:use|purposes?|research) only"
    r"|internal (?:research|evaluation) (?:purposes? )?only"
    r"|evaluation purposes only",
    re.IGNORECASE,
)

# A root LICENSE that only forwards elsewhere. Short, and it points at a path.
POINTER = re.compile(r"\bsee\b[^.\n]{0,60}(licen[sc]es?/|\.txt|\.md|directory|for details)", re.I)

# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------

WEIGHT_HOSTS: tuple[tuple[str, str, bool], ...] = (
    # (host substring, human name, needs a credential or manual step)
    ("huggingface.co", "Hugging Face", True),
    ("hf.co", "Hugging Face", True),
    ("ngc.nvidia.com", "NVIDIA NGC", True),
    ("api.ngc.nvidia.com", "NVIDIA NGC", True),
    ("drive.google.com", "Google Drive", True),
    ("onedrive.live.com", "OneDrive", True),
    ("zenodo.org", "Zenodo", False),
    ("figshare.com", "figshare", False),
    ("osf.io", "OSF", False),
    ("storage.googleapis.com", "Google Cloud Storage", False),
    ("amazonaws.com", "Amazon S3", False),
    ("dl.fbaipublicfiles.com", "Meta public files", False),
    ("data.pyg.org", "PyG wheels", False),
    ("download.pytorch.org", "PyTorch wheel index", False),
)

CREDENTIAL_VARS = re.compile(
    r"\b(HF_TOKEN|HUGGING_?FACE_?(?:HUB_?)?TOKEN|NGC_API_KEY|NGC_CLI_API_KEY"
    r"|WANDB_API_KEY|GITLAB_TOKEN|GITHUB_TOKEN|OPENAI_API_KEY|AWS_ACCESS_KEY_ID"
    r"|KAGGLE_KEY|EARTHDATA_TOKEN)\b"
)

WEIGHT_WORDS = re.compile(
    r"\b(weights?|checkpoints?|ckpts?|\.pt\b|\.pth\b|safetensors|params)\b", re.I
)
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|TB)\b", re.IGNORECASE)
DOWNLOAD_FILE = re.compile(r"(download|fetch|get)[_-]?.*\.(sh|py|bash)$|^download", re.IGNORECASE)

# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

ID_LIST_FILE = re.compile(r"(_ids?|ids?_list|accessions?|splits?|valid_ids?)\.(txt|csv|tsv)$", re.I)
CONTROLLED_ACCESS = re.compile(
    r"\bdbGaP\b|\bEGA\b(?!\w)|UK ?Biobank|controlled[- ]access|data use agreement|\bDUA\b"
    r"|available (?:from the authors? )?(?:up)?on (?:reasonable )?request"
    r"|apply for access|access (?:is )?granted",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# env
# --------------------------------------------------------------------------

INSTALL_CMD = re.compile(
    r"\b(?:uv\s+)?pip\s+install|\bconda\s+install|\bmamba\s+install|\bapt-get\s+install"
    r"|\buv\s+(?:sync|add)\b|\bpoetry\s+install|\bnpm\s+(?:i|install)\b|\bcmake\b|\bmake\s+install"
)
SWALLOWED = re.compile(r"\|\|\s*(?:true|:|echo\b|warn\b)|2>\s*/dev/null(?!\s*\|\|)")
TROUBLE_HEADING = re.compile(
    r"known issue|workaround|if the (?:install|build) fails"
    r"|troubleshoot|common (?:problems|errors)",
    re.IGNORECASE,
)
REQUIRES_PYTHON = re.compile(r"""requires-python\s*=\s*["']([^"']+)["']""")
PYTHON_REQUIRES = re.compile(r"""python_requires\s*=\s*["']([^"']+)["']""")
CONDA_PYTHON = re.compile(r"^\s*-\s*python\s*([<>=!~][^#\s]*)", re.MULTILINE)
CUDA_TAG = re.compile(r"\+cu(\d{2,3})|cuda[-_]?(\d+\.\d+)|cu(\d{3})\b", re.IGNORECASE)
TORCH_SPEC = re.compile(r"\btorch(?:vision|audio)?\s*[=><~]{1,2}\s*([\d.]+)")
GLIBC = re.compile(r"\bGLIBC\b|\bglibc\b")
UBUNTU = re.compile(r"Ubuntu\s*(\d{2}\.\d{2})\s*(\+|or (?:newer|later|above))?", re.IGNORECASE)
PIN = re.compile(r"[=~!<>]=|@\s*git\+|\bfrom\s+lock")

MANIFEST_GLOBS = (
    "pyproject.toml", "requirements*.txt", "requirements/*.txt", "setup.py", "setup.cfg",
    "environment*.yml", "environment*.yaml", "Pipfile", "conda*.yml", "conda*.yaml",
)
LOCKFILE_GLOBS = ("uv.lock", "poetry.lock", "Pipfile.lock", "conda-lock.yml", "requirements*.lock")

# --------------------------------------------------------------------------
# hardware
# --------------------------------------------------------------------------

# A figure only counts as video memory when something next to it says so. The
# bare word `memory` used to be an alternative here, which read "64 GB system
# memory" as a VRAM floor — a false block on hosts that would have run it, and
# a false pass whenever `min()` then picked a RAM figure below the real one.
VRAM_TOKEN = re.compile(
    r"\bVRAM\b|\bHBM\d*\b"
    r"|\b(?:GPU|video|device|graphics|card)\s+(?:memory|RAM)\b"
    r"|\bGPUs?\b(?=[^.\n]{0,40}?\d)",
    re.IGNORECASE,
)
VRAM_WINDOW = re.compile(r"[,;]| and ")
GPU_SKU = re.compile(
    r"\b(A100|H100|H200|L40S?|V100|A6000|RTX\s?\d{4}|T4|MI\d{3}X?)\b", re.IGNORECASE
)
GPU_NEEDED = re.compile(
    r"nvidia-smi|--gpus\b|torch\.cuda|device\s*=\s*[\"']cuda|cuda\.is_available|CUDA_VISIBLE_DEVICES"
    r"|\bgpus?_per_node\b|accelerator\s*=\s*[\"']gpu",
    re.IGNORECASE,
)
CLUSTER = re.compile(r"\bsbatch\b|\bsrun\b|#SBATCH|torchrun\b|--nnodes|\bslurm\b|multi-?node", re.I)

# --------------------------------------------------------------------------
# handoff
# --------------------------------------------------------------------------

AGENT_GUIDANCE = (
    ".claude/skills/*/SKILL.md",
    ".claude/commands/*.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".cursor/rules/*",
    ".cursorrules",
    ".github/copilot-instructions.md",
)


class SurveyError(Exception):
    """The repository could not be surveyed at all."""


class Repo:
    """A read-only, text-only view of a checkout."""

    def __init__(self, root: Path) -> None:
        self.root = root
        # Paths this view cannot speak for, and why. Read by `_record_gaps`.
        self.skipped: dict[str, str] = {}
        self.truncated = 0
        self.paths = self._index()
        self._cache: dict[str, str | None] = {}

    def _index(self) -> list[str]:
        found: list[str] = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            if path.is_symlink() and not path.is_file():
                # A link to a directory invites a cycle; a dangling one has
                # nothing behind it. File links are followed — repositories do
                # symlink their LICENSE, and dropping it silently produced a
                # blocking "no licence file anywhere" for a repository that
                # has one.
                self._skip(rel, "symlink to a directory or to nothing — not indexed")
                continue
            if not path.is_file():
                continue
            if len(found) >= MAX_FILES:
                self.truncated += 1
                continue
            found.append(rel)
        return found

    def _skip(self, rel: str, why: str) -> None:
        """Record a path this view could not read, and the reason.

        A file that was not read is not a file that said nothing, and nothing
        else in this program can tell the two apart: every check works from
        `read()` returning a string. `survey_repo` turns these into
        `inconclusive` entries, which `gate.py` turns into `unknown`.
        """
        self.skipped.setdefault(rel, why)

    def read(self, rel: str) -> str | None:
        """Text of one file, or None if it is binary, huge or unreadable."""
        if rel in self._cache:
            return self._cache[rel]
        text: str | None = None
        path = self.root / rel
        if Path(rel).suffix.lower() not in BINARY_SUFFIXES:
            try:
                size = path.stat().st_size
                if size <= MAX_FILE_BYTES:
                    text = path.read_text(encoding="utf-8", errors="replace")
                else:
                    self._skip(
                        rel,
                        f"{size / 1e6:.1f} MB, over the {MAX_FILE_BYTES / 1e6:.0f} MB read limit",
                    )
            except OSError as exc:
                self._skip(rel, f"could not be read ({exc.strerror or exc})")
        if text is not None and "\x00" in text[:4096]:
            self._skip(rel, "NUL bytes despite a text suffix — treated as binary")
            text = None
        self._cache[rel] = text
        return text

    def match(self, *patterns: str) -> list[str]:
        """Paths matching any glob, tested against both full path and basename."""
        hits: list[str] = []
        for rel in self.paths:
            base = rel.rsplit("/", 1)[-1]
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(base, p) for p in patterns):
                hits.append(rel)
        return hits

    def with_suffix(self, suffixes: frozenset[str]) -> list[str]:
        return [p for p in self.paths if Path(p).suffix.lower() in suffixes]

    def grep(self, pattern: re.Pattern[str], paths: list[str]) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        for rel in paths:
            text = self.read(rel)
            if not text:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    out.append((rel, number, line.strip()))
        return out

    def is_vendored(self, rel: str) -> bool:
        return any(part in VENDORED_DIRS for part in rel.split("/"))

    def docs(self) -> list[str]:
        return [
            p
            for p in self.with_suffix(DOC_SUFFIXES)
            if not self.is_vendored(p) and not _looks_like_license(p)
        ]

    def scripts(self) -> list[str]:
        shell = [p for p in self.with_suffix(SHELL_SUFFIXES) if not self.is_vendored(p)]
        docker = [
            p
            for p in self.match("Dockerfile*", "*.dockerfile", "Makefile")
            if not self.is_vendored(p)
        ]
        return sorted(set(shell + docker))

    def manifests(self) -> list[str]:
        return [p for p in self.match(*MANIFEST_GLOBS) if not self.is_vendored(p)]


class Survey:
    """Accumulates findings, and — just as importantly — what stayed unknown."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.findings: list[Finding] = []
        self.inconclusive: list[dict[str, str]] = []

    def add(self, **kwargs: Any) -> None:
        self.findings.append(Finding(**kwargs))

    def unknown(self, check: str, why: str) -> None:
        """Record a check that ran and could not reach a conclusion.

        This is the whole reason `gate.py` has an `unknown` verdict. A check
        that looked for a VRAM figure and found none has not established that
        the model is small.
        """
        self.inconclusive.append({"check": check, "why": why})


def _looks_like_license(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return bool(LICENSE_NAME.match(base) or LICENSE_DIR.search("/" + rel))


def _license_scope(rel: str, only: bool = False) -> str:
    """Which layer a licence file governs, read off its name and location.

    `only` says this is the repository's single licence file, in which case it
    governs everything and calling it the "code" layer understates it — the
    Proteina LICENSE covers source, weights and dataset indices alike, and a
    report that files its non-commercial clause under "code" invites the
    reader to assume the weights are free.
    """
    name = rel.lower()
    if only and "/" not in rel:
        return "repository"
    if any(key in name for key in ("third", "3rd", "party", "notice")):
        return "third-party"
    if any(key in name for key in ("weight", "model", "ckpt", "checkpoint")):
        return "weights"
    if any(key in name for key in ("dataset", "data")):
        return "data"
    # A licence inside a subdirectory that is not itself a licences/ folder
    # belongs to whatever vendored package lives there — `ProteinMPNN/LICENSE`
    # is that package's MIT, not a statement about this project's code.
    if "/" in rel and not LICENSE_DIR.search("/" + rel):
        return "third-party"
    return "code"


def _identify(text: str) -> list[str]:
    """Every licence family present, not the first one found.

    A `license_third_party.txt` is an aggregate: it lists what was borrowed
    and appends a copy of each licence. Stopping at the first signature
    reports such a file as Apache-2.0 and loses the MIT, the Beer-ware and
    whatever else is stacked underneath — which is the half a reader needs.
    """
    return [name for name, pattern in LICENSE_SIGNATURES if pattern.search(text)]


def _evidence(hits: list[tuple[str, int, str]], limit: int = 4) -> tuple[Evidence, ...]:
    """Evidence for a finding, one entry per distinct line.

    Two regex groups matching on the same line is an artefact of the pattern,
    not two independent observations, and citing the line twice makes a
    finding look better-supported than it is.
    """
    seen: dict[tuple[str, int], Evidence] = {}
    for rel, number, line in hits:
        seen.setdefault((rel, number), Evidence(path=rel, line=number, quote=line))
    return tuple(list(seen.values())[:limit])


def _first_line(text: str, pattern: re.Pattern[str]) -> tuple[int, str] | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return number, line.strip()
    return None


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_license(survey: Survey) -> None:
    repo = survey.repo
    files = [p for p in repo.paths if _looks_like_license(p) and not repo.is_vendored(p)]
    if not files:
        # "None found" and "none the index could open" are different claims,
        # and only the first justifies the strongest finding this script emits.
        unseen = sorted(rel for rel in repo.skipped if _looks_like_license(rel))
        if unseen:
            survey.unknown(
                "license.absent",
                f"no licence file could be read, and {', '.join(unseen)} was skipped by the "
                "file index — open it by hand before concluding there is no grant",
            )
            return
        survey.add(
            id="license.absent",
            layer="license",
            severity="blocking",
            summary="No licence file anywhere — the default is all rights reserved",
            detail=(
                "Public does not mean licensed. Without a grant you have no right to use, "
                "modify or redistribute this code, and journals increasingly ask. Open an "
                "issue asking the authors to add one before building on it."
            ),
            evidence=(Evidence(path=".", quote="no LICENSE, LICENCE, COPYING or licenses/ found"),),
        )
        return

    pointers = {
        rel
        for rel in files
        if "/" not in rel
        and not _identify(repo.read(rel) or "")
        and POINTER.search(repo.read(rel) or "")
        and len(repo.read(rel) or "") < 2000
    }

    # A licence at the root, or inside a licences/ folder, is this project
    # speaking. One buried in a vendored package is that package speaking, and
    # counting it would mean almost every repository has "several licences".
    first_party = [rel for rel in files if "/" not in rel or LICENSE_DIR.search("/" + rel)]

    scopes: dict[str, list[tuple[str, list[str]]]] = {}
    for rel in files:
        text = repo.read(rel) or ""
        found = _identify(text)
        scope = _license_scope(rel, only=len(first_party) == 1)
        if rel not in pointers:
            scopes.setdefault(scope, []).append((rel, found or ["unidentified"]))

        hit = _first_line(text, NONCOMMERCIAL)
        if hit:
            line, quote = hit
            survey.add(
                id=f"license.restricted.{scope}",
                layer="license",
                severity="blocking",
                summary=f"The {scope} licence restricts use to non-commercial or research purposes",
                detail=(
                    "This does not stop the code running. It governs what you may do with "
                    "what comes out — publishing a benchmark is usually fine, shipping a "
                    "product is not. Read the clause in full before you build on the result."
                ),
                evidence=(Evidence(path=rel, line=line, quote=quote),),
            )

        copyleft = [name for name in found if name in ("AGPL-3.0", "GPL")]
        if copyleft:
            survey.add(
                id=f"license.copyleft.{scope}",
                layer="license",
                severity="degraded",
                summary=f"The {scope} layer includes {', '.join(copyleft)} — strong copyleft "
                "reaches anything you link to it",
                detail="Fine for a private reproduction; a problem if the result ships inside "
                "something permissively licensed.",
                evidence=(Evidence(path=rel, line=1, quote=f"{', '.join(copyleft)} in {rel}"),),
            )

        if not found:
            survey.unknown(
                "license.identify",
                f"{rel} matches no licence signature this script knows — read it by hand",
            )

    for rel in sorted(pointers):
        text = repo.read(rel) or ""
        hit = _first_line(text, POINTER)
        line, quote = hit if hit else (1, text.strip()[:120])
        survey.add(
            id="license.root-is-a-pointer",
            layer="license",
            severity="degraded",
            summary=f"{rel} contains no licence terms — it forwards to other files",
            detail=(
                "Every automated licence check reads this file. Anything that scans a "
                "repository root — GitHub's own detector, pip-licenses, an SBOM tool — "
                "will report this project as unlicensed or unknown, and a human who reads "
                "only this file learns nothing. The real terms are in the files it names, "
                "and they are usually not all the same."
            ),
            evidence=(Evidence(path=rel, line=line, quote=quote),),
        )

    families = {
        scope: sorted({name for _, names in entries for name in names})
        for scope, entries in scopes.items()
    }
    own = {scope: names for scope, names in families.items() if scope != "third-party"}
    distinct = {f for names in own.values() for f in names if f != "unidentified"}
    if len(own) > 1 and len(distinct) > 1:
        survey.add(
            id="license.layers-differ",
            layer="license",
            severity="degraded",
            summary="Code, weights and data are under different licences — check each separately",
            detail=(
                "Layers found: "
                + "; ".join(f"{scope} → {', '.join(names)}" for scope, names in sorted(own.items()))
                + ". The permissive one at the top is not the one that governs the checkpoints."
            ),
            evidence=tuple(
                Evidence(path=rel, line=1, quote=f"{scope}: {', '.join(names)}")
                for scope, entries in sorted(scopes.items())
                if scope != "third-party"
                for rel, names in entries
            ),
        )

    vendored = [(rel, names) for rel, names in scopes.get("third-party", [])]
    extra = {f for _, names in vendored for f in names if f not in distinct and f != "unidentified"}
    if extra:
        survey.add(
            id="license.vendored-differs",
            layer="license",
            severity="note",
            summary=f"Bundled third-party code adds {', '.join(sorted(extra))}",
            detail=(
                "Copied-in dependencies keep their own terms, and those terms travel with "
                "anything you redistribute. Worth a look before publishing a derivative; "
                "irrelevant if you are only running the thing."
            ),
            evidence=tuple(
                Evidence(path=rel, line=1, quote=", ".join(names)) for rel, names in vendored[:4]
            ),
        )

    if len(own) == 1 and "weights" not in own and "repository" not in own:
        survey.unknown(
            "license.weights",
            "no licence file names the model weights — if this repository ships or "
            "downloads checkpoints, their terms are undocumented",
        )


def check_weights(survey: Survey) -> None:
    repo = survey.repo
    downloaders = [p for p in repo.match("*download*", "*fetch*") if not repo.is_vendored(p)]
    downloaders = [
        p for p in downloaders if Path(p).suffix.lower() in {".sh", ".py", ".bash", ".md"}
    ]

    # Grouped by provider, not by domain: NGC answers on two hostnames and
    # reporting it twice reads as two separate obstacles.
    hosts_seen: dict[str, list[str]] = {}
    gated_hits: dict[str, list[tuple[str, int, str]]] = {}
    candidates = sorted(set(downloaders + repo.scripts() + repo.docs() + repo.manifests()))
    for host, label, gated in WEIGHT_HOSTS:
        hits = repo.grep(re.compile(re.escape(host), re.IGNORECASE), candidates)
        if not hits:
            continue
        hosts_seen.setdefault(label, []).append(host)
        if gated:
            gated_hits.setdefault(label, []).extend(hits)

    for label, hits in sorted(gated_hits.items()):
        survey.add(
            id=f"weights.gated.{label.lower().replace(' ', '-')}",
            layer="weights",
            severity="degraded",
            summary=f"Artefacts are fetched from {label}, which can require a login or an "
            "approved account",
            detail=(
                "A gated fetch fails differently from a missing file: it returns an HTML "
                "login page or a 403, and a download loop that only checks the exit code "
                "will write it to disk and carry on. Confirm you can reach the host and "
                "that any acceptance click-through is done first."
            ),
            evidence=_evidence(hits, limit=3),
            requires={"network_hosts": sorted(set(hosts_seen[label]))},
        )

    credential_files = sorted(
        set(repo.match(".env*", "*.env", "env*.sh") + repo.docs() + repo.scripts())
    )
    creds = repo.grep(CREDENTIAL_VARS, credential_files)
    named = sorted({m for _, _, line in creds for m in CREDENTIAL_VARS.findall(line)})
    if named:
        survey.add(
            id="weights.credentials",
            layer="weights",
            severity="degraded",
            summary=f"Credentials expected in the environment: {', '.join(named)}",
            detail="Set these before the download step, not after it fails halfway through.",
            evidence=_evidence(creds),
            requires={"env_vars": named},
        )

    sizes: list[tuple[str, int, str, float]] = []
    for rel, number, line in repo.grep(SIZE, repo.docs()):
        if not WEIGHT_WORDS.search(line) and not re.search(r"download|disk|storage", line, re.I):
            continue
        for amount, unit in SIZE.findall(line):
            gb = float(amount) * (1024 if unit.upper() == "TB" else 1)
            sizes.append((rel, number, line, gb))
    if sizes:
        biggest = max(sizes, key=lambda item: item[3])
        survey.add(
            id="weights.disk",
            layer="weights",
            severity="note",
            summary=f"The documentation states downloads up to {biggest[3]:.0f} GB",
            detail="Largest single figure found near a download or weights mention; the total "
            "across every artefact may be higher.",
            evidence=(Evidence(path=biggest[0], line=biggest[1], quote=biggest[2]),),
            requires={"disk_gb": round(biggest[3], 1)},
        )
    else:
        survey.unknown("weights.disk", "no download size stated in the documentation")

    if not hosts_seen and not downloaders:
        survey.unknown(
            "weights.source",
            "no download script or known model host found — if this repository needs "
            "pretrained weights, the path to them is not written down",
        )


def check_data(survey: Survey) -> None:
    repo = survey.repo
    id_lists: list[tuple[str, int]] = []
    for rel in repo.match("*.txt", "*.csv", "*.tsv"):
        if repo.is_vendored(rel) or not ID_LIST_FILE.search(rel.rsplit("/", 1)[-1]):
            continue
        text = repo.read(rel)
        if not text:
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 100 and all(len(ln) < 120 for ln in lines[:200]):
            id_lists.append((rel, len(lines)))

    if id_lists:
        survey.add(
            id="data.accession-lists",
            layer="data",
            severity="blocking",
            targets=("training",),
            summary="The training set ships as accession lists, not as data",
            detail=(
                "These files identify the records; they do not contain them. Reproducing "
                "training means fetching every entry from its source database, at the "
                "version the authors used, and rebuilding the same splits — routinely the "
                "single largest cost in the whole exercise, and the step most likely to "
                "diverge silently when an entry has since been superseded or withdrawn. "
                "Inference is unaffected."
            ),
            evidence=tuple(
                Evidence(path=rel, line=1, quote=f"{count} identifiers, one per line")
                for rel, count in sorted(id_lists, key=lambda item: -item[1])[:4]
            ),
        )

    controlled = repo.grep(CONTROLLED_ACCESS, repo.docs())
    if controlled:
        survey.add(
            id="data.controlled-access",
            layer="data",
            severity="blocking",
            targets=("training",),
            summary="Some data is behind an access application or a use agreement",
            detail="Approval takes weeks and may be refused. Confirm you have it before "
            "planning any run that depends on this data.",
            evidence=_evidence(controlled),
        )


def check_env(survey: Survey) -> None:
    repo = survey.repo
    manifests = repo.manifests()
    scripts = repo.scripts()

    _check_python_constraint(survey, manifests)
    _check_swallowed_failures(survey, scripts)
    _check_readme_only_fixes(survey, scripts)
    _check_torch_outside_manifest(survey, manifests, scripts)
    _check_pinning(survey, manifests)
    _check_os_constraint(survey)


def _check_python_constraint(survey: Survey, manifests: list[str]) -> None:
    repo = survey.repo
    for rel in manifests:
        text = repo.read(rel) or ""
        for pattern in (REQUIRES_PYTHON, PYTHON_REQUIRES, CONDA_PYTHON):
            match = pattern.search(text)
            if not match:
                continue
            spec = match.group(1).strip()
            line = text[: match.start()].count("\n") + 1
            survey.add(
                id="env.python",
                layer="env",
                severity="note",
                summary=f"Requires Python {spec}",
                evidence=(Evidence(path=rel, line=line, quote=match.group(0).strip()),),
                requires={"python": spec},
            )
            return
    survey.unknown("env.python", "no Python version constraint declared in any manifest")


def _check_swallowed_failures(survey: Survey, scripts: list[str]) -> None:
    repo = survey.repo
    hits = [
        (rel, number, line)
        for rel, number, line in repo.grep(SWALLOWED, scripts)
        if INSTALL_CMD.search(line)
    ]
    if not hits:
        return
    survey.add(
        id="env.swallowed-install-failure",
        layer="env",
        severity="degraded",
        summary=f"{len(hits)} install step(s) discard their own failure and let the build continue",
        detail=(
            "`|| echo`, `|| true` and a redirected stderr all turn a failed install into a "
            "zero exit code. The build reports success, the package is absent, and the run "
            "dies hundreds of steps later inside an import whose traceback points somewhere "
            "unrelated. Before trusting the environment, import each of these by hand."
        ),
        evidence=_evidence(hits, limit=6),
    )


def _check_readme_only_fixes(survey: Survey, scripts: list[str]) -> None:
    """Fixes documented in prose that never made it into the build script."""
    repo = survey.repo
    script_text = "\n".join((repo.read(rel) or "") for rel in scripts)
    normalised = " ".join(script_text.split())

    for rel in repo.docs():
        text = repo.read(rel)
        if not text:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not TROUBLE_HEADING.search(line):
                continue
            for command_line, number in _fenced_commands(lines, index):
                if not INSTALL_CMD.search(command_line):
                    continue
                if " ".join(command_line.split()) in normalised:
                    continue
                survey.add(
                    id="env.fix-only-in-prose",
                    layer="env",
                    severity="degraded",
                    summary="A documented install fix is not in the build script — you "
                    "must apply it by hand",
                    detail=(
                        "The documentation describes a failure and gives the command that "
                        "avoids it, but the build script still contains the version that "
                        "fails. Running the script as published reproduces the known "
                        "problem. Heuristic: this command does not appear in any shell "
                        "script or Dockerfile in the repository — confirm before acting."
                    ),
                    evidence=(Evidence(path=rel, line=number, quote=command_line.strip()),),
                )
                return


def _unquote(line: str) -> str:
    return re.sub(r"^\s*>+\s?", "", line)


def _fenced_commands(lines: list[str], start: int, window: int = 40) -> list[tuple[str, int]]:
    """Command lines inside the first fenced block following `start`.

    Blockquote markers are stripped first. A "known issue" note is very often
    written as a `>` callout, fence and all, and a fence detector that does not
    strip `>` never enters the block — so the check would report nothing
    exactly where the pattern it hunts for is most common.
    """
    out: list[tuple[str, int]] = []
    inside = False
    for offset in range(start, min(start + window, len(lines))):
        line = _unquote(lines[offset])
        if line.lstrip().startswith("```"):
            if inside:
                break
            inside = True
            continue
        if inside and line.strip() and not line.lstrip().startswith("#"):
            out.append((line, offset + 1))
    return out


def _check_torch_outside_manifest(survey: Survey, manifests: list[str], scripts: list[str]) -> None:
    repo = survey.repo
    in_manifest = repo.grep(TORCH_SPEC, manifests)
    in_scripts = repo.grep(TORCH_SPEC, scripts)
    if in_scripts and not in_manifest:
        survey.add(
            id="env.torch-outside-manifest",
            layer="env",
            severity="degraded",
            summary="PyTorch is installed by a shell script, not declared in any manifest",
            detail=(
                "`pip install -e .` or `uv sync` alone will not give you this build — the "
                "CUDA-tagged wheel comes from a separate index that only the shell script "
                "knows about. Installing from the manifest silently yields a different "
                "torch, often a CPU one, and the first failure is at run time."
            ),
            evidence=_evidence(in_scripts, limit=3),
        )

    cuda_hits = repo.grep(CUDA_TAG, scripts + manifests)
    found: set[str] = set()
    for _, _, line in cuda_hits:
        for match in CUDA_TAG.findall(line):
            version = _cuda_version(match)
            if version:
                found.add(version)
    versions = sorted(found)
    if versions:
        survey.add(
            id="hardware.cuda",
            layer="hardware",
            severity="note",
            summary=f"Built against CUDA {', '.join(versions)}",
            evidence=_evidence(cuda_hits, limit=3),
            requires={"cuda_min": versions[0]},
        )
    else:
        survey.unknown("hardware.cuda", "no CUDA version pinned in the build")


def _cuda_version(match: tuple[str, ...]) -> str | None:
    for group in match:
        if not group:
            continue
        if "." in group:
            return group
        if len(group) >= 3:
            return f"{group[:2]}.{group[2:]}"
        return f"{group[0]}.{group[1:]}"
    return None


def _check_pinning(survey: Survey, manifests: list[str]) -> None:
    repo = survey.repo
    if repo.match(*LOCKFILE_GLOBS):
        return
    total = pinned = 0
    example: tuple[str, int, str] | None = None
    for rel in manifests:
        if Path(rel).name in {"setup.py", "setup.cfg"}:
            continue
        text = repo.read(rel) or ""
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip().strip('",\'')
            if not stripped or stripped.startswith("#") or "=" not in line and "\"" not in line:
                continue
            if not re.match(r"^[A-Za-z][\w.\-\[\]]*", stripped):
                continue
            if not re.search(r"[\w\]]\s*[=><~]", stripped) and not re.match(
                r"^[A-Za-z][\w.\-\[\]]*$", stripped
            ):
                continue
            total += 1
            if PIN.search(stripped):
                pinned += 1
            elif example is None:
                example = (rel, number, stripped)
    if total >= 10 and pinned * 2 < total and example:
        survey.add(
            id="env.unpinned",
            layer="env",
            severity="note",
            summary=f"No lockfile, and {total - pinned} of {total} dependency lines float",
            detail="Whatever resolved for the authors is not what will resolve for you. "
            "Capture the resolved set once it works, so the next attempt is repeatable.",
            evidence=(Evidence(path=example[0], line=example[1], quote=example[2]),),
        )


def _check_os_constraint(survey: Survey) -> None:
    repo = survey.repo
    docs = repo.docs()
    glibc_hits = repo.grep(GLIBC, docs)
    ubuntu_hits = repo.grep(UBUNTU, docs)
    if not glibc_hits and not ubuntu_hits:
        return

    requires: dict[str, Any] = {"os": "linux"}

    # Only a version carrying its own "+" or "or newer" is a floor, and where
    # several are stated the highest binds. Both halves matter: a single line
    # routinely reads "Requires Ubuntu 22.04+ … Ubuntu 20.04 throws GLIBC
    # errors", so testing the whole line for a "+" promotes the version that
    # is documented as *failing*, and taking the minimum then adopts it as the
    # requirement — a floor two releases below the real one, arrived at from
    # the sentence that says so.
    floors = [
        version for _, _, line in ubuntu_hits for version, plus in UBUNTU.findall(line) if plus
    ]
    known = [v for v in floors if v in _UBUNTU_GLIBC]
    if known:
        newest = max(known, key=lambda v: tuple(map(int, v.split("."))))
        requires["glibc_min"] = _UBUNTU_GLIBC[newest]
    elif floors:
        survey.unknown(
            "env.glibc",
            f"a Linux floor is stated ({', '.join(sorted(set(floors)))}) but its glibc "
            "version is not in this script's table — check it by hand",
        )

    evidence = _evidence(glibc_hits + ubuntu_hits, limit=3)
    survey.add(
        id="env.os-constraint",
        layer="env",
        severity="note",
        summary="The documentation names a required Linux distribution or a GLIBC floor",
        detail="A GLIBC mismatch shows up as an import-time symbol error, not as an install "
        "failure — the container path exists for exactly this reason.",
        evidence=evidence,
        requires=requires,
    )


_UBUNTU_GLIBC = {"18.04": "2.27", "20.04": "2.31", "22.04": "2.35", "24.04": "2.39"}


def _vram_figures(line: str) -> list[float]:
    """GB figures on this line that are actually about video memory.

    Taking every size on a matching line read "24 GB VRAM, 64 GB system RAM"
    as two VRAM figures, and the smaller-is-the-minimum rule below then gated
    on the wrong one. So the line is cut into clauses and a figure only counts
    inside a clause that says what the memory is for. Markdown tables get one
    allowance, because `| VRAM | 24 GB |` puts the label and the number in
    adjacent cells.
    """
    stripped = line.strip()
    if stripped.startswith("|") and stripped.count("|") >= 2:
        cells = stripped.strip("|").split("|")
        clauses = [f"{a} {b}" for a, b in zip(cells, cells[1:])] or cells
    else:
        clauses = VRAM_WINDOW.split(stripped)

    figures: list[float] = []
    for clause in clauses:
        if not VRAM_TOKEN.search(clause):
            continue
        figures += [
            float(amount)
            for amount, unit in SIZE.findall(clause)
            if unit.upper() == "GB" and 4 <= float(amount) <= 200
        ]
    return figures


def check_hardware(survey: Survey) -> None:
    repo = survey.repo
    docs = repo.docs()
    code = [
        p
        for p in repo.paths
        if Path(p).suffix in {".py", ".sh", ".yaml", ".yml"} and not repo.is_vendored(p)
    ]

    gpu_hits = repo.grep(GPU_NEEDED, code + docs)
    if gpu_hits:
        survey.add(
            id="hardware.gpu",
            layer="hardware",
            severity="note",
            summary="A CUDA device is required",
            evidence=_evidence(gpu_hits, limit=3),
            requires={"gpu": True},
        )

    vram: list[tuple[str, int, str, float]] = []
    for rel, number, line in repo.grep(VRAM_TOKEN, docs):
        for amount in _vram_figures(line):
            vram.append((rel, number, line, amount))
    if vram:
        smallest = min(vram, key=lambda item: item[3])
        largest = max(vram, key=lambda item: item[3])
        survey.add(
            id="hardware.vram",
            layer="hardware",
            severity="note",
            summary=f"Documented VRAM figures run from {smallest[3]:.0f} to {largest[3]:.0f} GB",
            detail="Gated on the smallest stated figure, which is usually the minimum rather "
            "than the recommendation. Meeting the minimum and nothing more tends to mean "
            "reducing batch size, which changes throughput but not the result.",
            evidence=_evidence([(rel, number, line) for rel, number, line, _ in vram], limit=3),
            requires={"vram_gb": smallest[3]},
        )
    elif gpu_hits:
        skus = repo.grep(GPU_SKU, docs)
        if skus:
            survey.add(
                id="hardware.gpu-sku",
                layer="hardware",
                severity="note",
                summary="The documentation names specific datacentre GPUs but states no "
                "VRAM figure",
                evidence=_evidence(skus, limit=3),
            )
        survey.unknown(
            "hardware.vram",
            "a GPU is required but no VRAM figure is documented — size it from the "
            "checkpoint before booking anything",
        )

    cluster = repo.grep(CLUSTER, repo.scripts() + docs)
    if cluster:
        survey.add(
            id="hardware.cluster",
            layer="hardware",
            severity="degraded",
            targets=("training",),
            summary="Training is written for a multi-node cluster",
            detail="Single-node reproduction of a multi-node run changes the effective batch "
            "size, and with it the learning-rate schedule the paper reports.",
            evidence=_evidence(cluster, limit=3),
        )


def check_handoff(survey: Survey) -> None:
    repo = survey.repo
    guidance = [p for p in repo.match(*AGENT_GUIDANCE) if not repo.is_vendored(p)]
    if guidance:
        survey.add(
            id="handoff.repo-ships-guidance",
            layer="handoff",
            severity="note",
            summary="This repository ships its own agent instructions "
            f"({len(guidance)} file(s)) — read them first",
            detail=(
                "Whoever wrote these knows the pipeline better than any general-purpose "
                "survey can. Use them for how to run the thing; the checks here cover what "
                "they typically do not — licence layers, credentials, and whether this host "
                "qualifies at all."
            ),
            evidence=tuple(
                Evidence(path=rel, line=1, quote="agent guidance") for rel in guidance[:6]
            ),
        )

    if not repo.match(".github/workflows/*"):
        survey.add(
            id="handoff.no-ci",
            layer="handoff",
            severity="note",
            summary="No CI configuration — nothing upstream checks that this still builds",
            detail="Every dependency that has drifted since the last manual run has drifted "
            "unnoticed. Expect to fix the environment, not just configure it.",
            evidence=(Evidence(path=".github", quote="no workflows directory"),),
        )

    own_tests = [
        p for p in repo.match("test_*.py", "*_test.py", "tests/*", "conftest.py")
        if not repo.is_vendored(p)
    ]
    if not own_tests:
        survey.add(
            id="handoff.no-tests",
            layer="handoff",
            severity="note",
            summary="No test suite of its own — there is no cheap way to tell a broken "
            "install from a broken model",
            evidence=(
                Evidence(path=".", quote="no test_*.py or tests/ outside vendored dirs"),
            ),
        )


CHECKS = (check_license, check_weights, check_data, check_env, check_hardware, check_handoff)


def _record_gaps(survey: Survey) -> None:
    """Turn what the file view could not see into inconclusive entries.

    Runs after the checks, because `Repo.read` is lazy: until a check asks for
    a file, nothing has failed to read it. Without this the two most damaging
    silences in the whole script were invisible — an accession list over the
    size limit dropped its blocking finding, and an unreadable build script
    dropped three env findings, both reported as a clean survey.
    """
    repo = survey.repo
    if repo.truncated:
        survey.unknown(
            "files.index-truncated",
            f"the index stopped at {MAX_FILES} files with {repo.truncated} left over — "
            "every check below read a partial tree",
        )
    if repo.skipped:
        shown = sorted(repo.skipped.items())[:6]
        listed = "; ".join(f"{rel} ({why})" for rel, why in shown)
        rest = len(repo.skipped) - len(shown)
        more = f"; and {rest} more" if rest else ""
        survey.unknown(
            "files.unread",
            f"{len(repo.skipped)} file(s) could not be read: {listed}{more}",
        )


def git_facts(root: Path) -> dict[str, Any]:
    """Best-effort provenance. Absent git, absent answers — never a guess."""

    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() or None if done.returncode == 0 else None

    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "committed": run("log", "-1", "--format=%cI"),
        "remote": run("config", "--get", "remote.origin.url"),
        "describe": run("describe", "--tags", "--always"),
    }


def survey_repo(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise SurveyError(f"{root} is not a directory")
    repo = Repo(root)
    if not repo.paths:
        raise SurveyError(f"{root} contains no readable files")

    survey = Survey(repo)
    for check in CHECKS:
        check(survey)
    _record_gaps(survey)

    return {
        "schema": SCHEMA,
        "repo": {
            "root": str(root.resolve()),
            "name": root.resolve().name,
            "files_indexed": len(repo.paths),
            "files_unread": len(repo.skipped),
            "files_over_index_limit": repo.truncated,
            "git": git_facts(root),
        },
        "findings": [f.to_dict() for f in by_severity(survey.findings)],
        "inconclusive": survey.inconclusive,
    }


def render_text(payload: dict[str, Any]) -> str:
    findings = [Finding.from_dict(raw) for raw in payload.get("findings", [])]
    lines = [f"{payload['repo']['name']} — {len(findings)} finding(s)", ""]
    for finding in findings:
        where = ", ".join(e.where() for e in finding.evidence[:2])
        lines.append(f"[{finding.severity:<8}] {finding.layer:<8} {finding.summary}")
        lines.append(f"           {where}")
    inconclusive = payload.get("inconclusive", [])
    if inconclusive:
        lines += ["", f"Inconclusive ({len(inconclusive)}):"]
        lines += [f"  - {item['check']}: {item['why']}" for item in inconclusive]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", help="path to a local checkout")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--out", help="write JSON here as well as to stdout")
    args = parser.parse_args(argv)

    if re.match(r"^(https?://|git@|github\.com/)", args.repo):
        print(
            "survey.py reads a local checkout; it does not clone.\n"
            f"  git clone --depth 1 {args.repo}\n"
            "then point this script at the resulting directory.",
            file=sys.stderr,
        )
        return 2

    try:
        payload = survey_repo(Path(args.repo))
    except (SurveyError, FindingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "text":
        sys.stdout.write(render_text(payload))
        if args.out:
            write_json(payload, args.out)
    else:
        sys.stdout.write(write_json(payload, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

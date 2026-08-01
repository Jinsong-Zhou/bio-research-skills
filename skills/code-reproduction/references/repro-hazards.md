# Reproduction hazards

What each check hunts for, why it is worth a check rather than a glance, and
what it looks like in the wild.

Worked evidence throughout is [`NVIDIA-BioNeMo/Proteina-Complexa`](https://github.com/NVIDIA-BioNeMo/Proteina-Complexa)
at commit `916eaae` (branch `dev`, read 2026-07-31) — a well-documented,
actively maintained repository from a strong lab, which is the point. These
are not the failures of a careless project. `tests/test_live_upstream.py`
re-checks eight of the claims below against the live repository — the default
branch, the licence layout, the two swallowed install steps, the prose-only
fix, the torch line, the accession-list sizes, and the absence of CI. When
upstream fixes one of those, that test fails and this file needs editing. The
rest of this page is not pinned that way; treat it as read on the date above.

---

## The clone does not land where you expect

The default branch here is **`dev`**. A `master` also answers; `main` 404s.
Anything that hardcodes `main` — a CI template, a `raw.githubusercontent.com`
URL, a docs link — points at nothing.

The README's own clone URL still names `NVIDIA-Digital-Bio`, an organisation
that has since become `NVIDIA-BioNeMo`. GitHub resolves both by repository ID,
so the old name keeps working for clones and raw fetches alike; what it costs
you is search. Grepping your notes for the current name will not find the
issue thread, and the two names appear interchangeably in the wild.

Checking the branch is cheap, and it invalidates every path you were about to
read:

```bash
gh api repos/OWNER/NAME --jq '.default_branch'
```

`survey.py` records the branch it read, so a report cannot be mistaken for one
about a different tree.

## The `LICENSE` file contains no licence

`LICENSE` is three lines:

> This repository contains multiple components covered by different licenses.
>
> See the licenses/ directory for details.

The terms live in four files under `licenses/`, and they are not the same as
each other. GitHub's detector reports `NOASSERTION`. Full treatment in
[license-layers.md](license-layers.md) — including the two sibling
repositories where the same lab uses a different layout again, and where the
commercial-use answer flips.

## The build script discards its own failures

Two install steps in `env/build_uv_env.sh` (lines 174 and 196 at `916eaae`)
end with `|| echo`:

```bash
uv pip install "atomworks[ml,openbabel,dev]" || echo "Warning: atomworks install failed"
uv pip install "git+https://github.com/uw-ipd/tmol.git@d8a6f7f…" || echo "Warning: tmol install failed"
```

`|| echo`, `|| true` and a redirected stderr all convert a failed install into
a zero exit code. The script prints "Installation Complete!", the package is
absent, and the failure arrives hundreds of steps later inside an import whose
traceback points somewhere unrelated. This is the same defect class as an API
returning HTTP 200 on error, and it earns the same treatment: verify the
result, never the status.

**After any build, import the swallowed packages by hand.** The survey lists
which ones.

## The fix is in the prose, not in the script

The README documents that tmol fails to install on Python 3.12 — and gives the
patch **you must apply to the build script yourself**:

```bash
if [[ "$PYTHON_VERSION" == "3.12" ]]; then
    uv pip install "llvmlite>=0.41" "numba>=0.59" || true
fi
```

The script as published does not contain it, and Python 3.12 is the script's
own default. Upstream is careful to say this hits "some users" and depends on
which `llvmlite` and `numba` wheels resolve for you — which is worse than a
deterministic failure, not better: the build succeeds on the machine where
someone checks, and fails on yours, and the difference is invisible in both
logs.

Two details make this hard to spot by eye. The block sits inside a `>`
blockquote callout, fence and all — so a fence detector that does not strip
the quote marker never enters it. And the workaround itself ends in `|| true`,
so even the fix fails silently.

## The manifest is not the environment

`pyproject.toml` declares neither `torch` nor any CUDA build. PyTorch is
installed only by `env/build_uv_env.sh:150`:

```bash
uv pip install torch==2.7.0+cu126 torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu126
```

So `pip install -e .` or `uv sync` — the two commands anyone reaches for
first — resolve a *different* torch from PyPI, usually a CPU build, and the
first symptom is at run time. Related pins in the same repository show how
much of an environment lives outside the manifest:

- `lightning>=2.5.0,<2.6  # 2.6.1 caused loading weight issue` — an upper
  bound derived from a bug, not from an API change
- `biotite>=0.41.0` in `pyproject.toml`, force-reinstalled to `1.6.0` at the
  end of the build script, with a comment saying pinning it earlier conflicts
- `graphein==1.7.7 --no-deps`, `--index-strategy unsafe-best-match`, a
  `jaxlib` from a Google Storage URL, a git SHA for tmol

None of this is reconstructible from the manifest. **The build script is the
manifest.** Read it before estimating anything.

## The dataset is a list of accession numbers

```
assets/data/pdb_multimer_ids.txt      45,856 lines
assets/data/plinder_valid_ids.txt     78,368 lines
```

These identify the records. They do not contain them. Reproducing training
means fetching every entry from PDB and PLINDER, at the version the authors
used, and rebuilding the same splits — routinely the largest single cost in
the exercise, and the step most likely to diverge silently when an entry has
since been superseded or withdrawn.

This is why **inference is the default target**. Inference is unaffected: it
needs the released checkpoints and a handful of target structures. Training
needs a dataset you must first reconstruct. Reporting them under one verdict
would let a green light for the first read as a green light for the second, so
`gate.py` keeps them apart and prints what it did not evaluate.

## Access gates, not compute gates

The obstacles here are permissions, not FLOPs:

| Gate | This repository |
|---|---|
| Credentials | `HF_TOKEN`, `GITLAB_TOKEN`, `WANDB_API_KEY` in `.env_example` |
| Hosts | NGC and Hugging Face, plus AWS and GitHub for community models |
| Disk | ~50 GB of outputs for a single design run, 200 GB for a sweep — this is space the run *produces*, quoted separately from anything it downloads |
| OS | Ubuntu 22.04+; 20.04 fails with GLIBC errors, hence the Docker path |
| GPU | 24–80 GB VRAM depending on pipeline; single-GPU per job |

A gated fetch fails differently from a missing file: it returns an HTML login
page or a 403, and a download loop checking only the exit code writes that to
disk and continues. If a checkpoint is implausibly small, read the first bytes
before debugging the model.

Reachability is worth probing rather than assuming — `huggingface.co` in
particular is unreachable from some networks, and that is a blocked
reproduction with no error message that says so.

## Nothing upstream checks that it still builds

No `.github/workflows`. The only `test_*.py` in the tree belongs to a vendored
copy of ColabDesign. Neither is a criticism — research repositories are not
products — but both are load-bearing for an estimate:

- Every dependency that drifted since the last manual run drifted unnoticed.
  Expect to *fix* the environment, not merely configure it.
- With no test suite, there is no cheap way to tell a broken install from a
  broken model. The first working run is also the first evidence that the
  install was correct.

## When the repository ships its own instructions

This one ships five Claude skills under `.claude/skills/` plus a shared
`preflight.sh` and `write_manifest.py`, documented in `docs/AGENT_SKILLS.md`.
They are good, and they know the pipeline far better than any general survey.

**Read them and prefer them** for how to run anything. What they do not cover
— and what repo-shipped instructions almost never cover — is the licence
layering, the credential inventory, and whether the host in front of you
qualifies at all. That gap is this skill's job.

---

## The check list

| Check | Hunts for | Blocking? |
|---|---|---|
| `license.absent` | no grant at all — all rights reserved by default | yes |
| `license.restricted.*` | non-commercial or research-only terms | yes, for use of output |
| `license.root-is-a-pointer` | a root LICENSE with no terms in it | no, but every scanner is now wrong |
| `license.layers-differ` | code, weights and data under different grants | no |
| `license.vendored-differs` | bundled dependencies adding other terms | no |
| `weights.gated.*` | fetches from a host that can demand a login | no |
| `weights.credentials` | tokens expected in the environment | once probed, yes |
| `weights.disk` | stated download volume | against free space |
| `data.accession-lists` | a "dataset" that is an index | training only |
| `data.controlled-access` | applications, DUAs, "on request" | training only |
| `env.swallowed-install-failure` | `\|\| echo`, `\|\| true` on install lines | no |
| `env.fix-only-in-prose` | a documented fix absent from the script | no |
| `env.torch-outside-manifest` | the environment living in a shell script | no |
| `env.python` / `env.os-constraint` | interpreter, distribution, GLIBC floor | against the host |
| `env.unpinned` | no lockfile and mostly floating versions | no |
| `hardware.gpu` / `hardware.vram` / `hardware.cuda` | what the docs assume you have | against the host |
| `hardware.cluster` | a schedule written for multi-node | training only |
| `handoff.repo-ships-guidance` | instructions to defer to | no |
| `handoff.no-ci` / `handoff.no-tests` | nobody upstream is checking | no |

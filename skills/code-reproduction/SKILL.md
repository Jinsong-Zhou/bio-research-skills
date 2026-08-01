---
name: code-reproduction
description: Decide whether a research code repository can actually be run here before committing time to it — read its licence layers separately (code, weights, data, third-party), find the credentials, downloads and GPUs it will demand, name the install failures its build script swallows, and gate all of that against this machine. Defaults to reproducing the inference path; covers training only when asked. Use when handed a GitHub link from a paper and asked whether it can be reproduced, what it needs, what it will cost, whether the weights may be used commercially, or why it will not build. Triggers on "can I reproduce this", "can I run this repo", "复现这篇论文的代码", "what do I need to run this", "will this run on my machine", "what licence are these weights", "is this repo usable", "why won't this install", or any paper-plus-repository pair where the question is feasibility rather than science.
license: MIT
allowed-tools: Bash Read Write
compatibility: Needs Python 3.9+ and `git` to clone the repository under review. The bundled scripts use only the standard library — no packages to install, no credentials required. `probe.py` reads `nvidia-smi` when present and degrades cleanly when it is not; it opens a network connection only when you pass `--reach`, and it records whether a credential is set, never its value. Running the repository itself is out of scope and is handed to other skills — see references/handoff.md.
metadata:
  version: "0.1"
  skill-author: "Jinsong Zhou"
---

# Code reproduction

A link at the end of a paper is not a reproduction. Between the two sit a
licence that may forbid what you intend, weights behind a login, a dataset
that shipped as a list of accession numbers, a CUDA build the manifest does
not mention, and a GPU you may not have. Each is cheap to check and expensive
to discover — the expensive discovery being the one that happens four hours
in.

This skill answers one question: **should you start?** It does not run the
model. When the answer is yes, it hands over to a skill that does.

## Scope

**Inference is the default target.** Reproducing inference — released weights,
released data, a published number — is what people attempt, and the obstacles
are obstacles of access. Training is a different exercise with different
obstacles, and it is opt-in: pass `--target training` only when the user has
asked for training specifically. Never quietly widen the scope; a report that
says `ok` for inference says nothing about training, and `gate.py` prints that
sentence for you.

**This skill does not execute the repository.** No environment is built, no
weights are downloaded, no model is run. Everything below reads files and
inspects the local machine.

## Procedure

### 1. Establish the target

Inference unless the user said training. If they said "reproduce the paper"
without qualifying it, that is inference — say so in one line so they can
correct you.

### 2. Get the repository

```bash
git clone --depth 1 https://github.com/OWNER/NAME /tmp/repro/NAME
```

Do not assume the default branch is `main`. If the clone 404s, or if a
documented file is missing, check what the default actually is — research
repositories sit on `dev`, `master`, `public` and `release` at least as often.
`survey.py` records the branch it read so the report cannot be mistaken for a
different one.

If the user already has a checkout, survey that instead: what is on their disk
is what they will run.

### 3. Look for the repository's own instructions first

```bash
ls .claude/skills/*/SKILL.md AGENTS.md CLAUDE.md .cursor/rules 2>/dev/null
```

If any exist, **read them and prefer them** for anything about how to run the
pipeline. Whoever wrote them knows it better than any general survey. This
skill then covers what such files almost never do: the licence layers, the
credentials, and whether this host qualifies. `survey.py` reports them as
`handoff.repo-ships-guidance` and `gate.py` puts the pointer above the gates.

### 4. Survey the repository

```bash
python3 scripts/survey.py /tmp/repro/NAME --out survey.json --format text
```

Six groups of checks — licence, weights, data, env, hardware, handoff — each
producing findings that name the file and line they came from. Anything a
check could not settle is recorded under `inconclusive` rather than dropped.

### 5. Probe this machine

```bash
python3 scripts/probe.py --from-survey survey.json --disk /path/for/weights --out probe.json --format text
```

`--from-survey` makes the probe check exactly the credentials and hosts the
repository asked for. Without it those come back `unknown`, which is honest
but less useful. `--disk` should point where the weights will actually land,
not the current directory.

The probe reads whether a credential is set. It never reads the value, so
`probe.json` is safe to keep alongside a reproduction log.

### 6. Gate

```bash
python3 scripts/gate.py --survey survey.json --probe probe.json --target inference --out REPRODUCTION.md
```

Exit status is 1 when the verdict is `blocked` or `unknown`, 0 otherwise.

## Reading the verdict

| Verdict | What it means | What to do |
|---|---|---|
| `blocked` | A requirement is stated and this host does not meet it | Stop. Report the specific gates and what would clear them |
| `unknown` | A requirement or a capability could not be determined | Resolve it before committing time — an unknown is not a pass |
| `degraded` | It will run, with a documented problem ahead | Proceed, having read the problems |
| `ok` | Every stated requirement found was met | Proceed |

`unknown` ranks worse than `degraded` on purpose. The failure this skill
exists to prevent is a report that reads clear because a check quietly found
nothing, so a survey that could not find a VRAM figure must not be reported as
a model that fits.

## Reporting

Always give the user three things, in this order:

1. **The verdict and the one thing that decides it.** Not a list — the single
   gate that matters most. "Blocked: no CUDA device, and the pipeline is
   single-GPU CUDA throughout."
2. **The licence summary**, every time, even when the verdict is `ok`. Name
   each layer and its terms: code, weights, data, third-party. This is the
   only finding that can invalidate work *after* it succeeds, so it does not
   get filed under "details". See
   [references/license-layers.md](references/license-layers.md).
3. **What it would take to clear the blocks**, concretely. "A single A100 or
   H100 with 40 GB, Ubuntu 22.04+, an `HF_TOKEN`, and 200 GB of disk" is
   actionable; "you need a better machine" is not.

Then, if the user wants to go ahead, hand over —
[references/handoff.md](references/handoff.md) names the skills that run
repositories and their licences. Do not reimplement what they do.

## When the verdict is blocked

Say what is blocked and stop. Do not offer to "try anyway" — the point of the
check is that trying anyway is the expensive path. Two blocks are worth
special handling:

- **No GPU on this host.** The repository is not unreproducible; this machine
  is unsuitable. Say which card would do, and what it would cost to rent one.
  Offer to survey against a different host later: `survey.json` is portable,
  so only `probe.py` and `gate.py` need re-running there.
- **A restricted licence.** This blocks *use of the output*, not execution.
  Be precise about which: an academic user reading "blocked" on a
  research-only weights licence may be perfectly entitled to proceed.

## When the verdict is unknown

Name the specific unknown and how to settle it. Most resolve in one command —
`nvidia-smi`, `ldd --version`, `df -h`, or reading one paragraph of a model
card. Do not average an unknown into an overall impression.

## What this cannot see

State these plainly rather than letting silence imply coverage:

- **Whether the code is correct**, or whether the published numbers are
  reproducible at all. This checks access, not science. Use
  `paper-deep-reading` to judge whether the claims were supported in the first
  place.
- **Undocumented requirements.** A model that needs 60 GB while the README
  says 24 will pass this gate and fail at run time. The survey reads what the
  repository says about itself.
- **Licence meaning.** The layers and their families are identified; what a
  clause permits in a given jurisdiction is a question for a human, and for
  anything commercial, a lawyer.
- **Anything behind a login.** Whether an account has been granted access to a
  gated model is not visible from outside it.

## References

- [references/license-layers.md](references/license-layers.md) — the four
  layers, how to read each, and the traps that recur
- [references/repro-hazards.md](references/repro-hazards.md) — the catalogue
  of failure modes each check hunts for, with worked evidence
- [references/handoff.md](references/handoff.md) — which existing skill to
  hand execution to, and under what licence

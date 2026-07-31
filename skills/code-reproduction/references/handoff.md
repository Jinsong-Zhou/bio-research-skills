# Handing the run over

This skill decides whether to start. It does not build environments, download
weights, or run models — and it should not grow into something that does,
because other people have already built that and shipped it under MIT.

Once the gate is `ok` or `degraded` and the user wants to proceed, hand over.
Give whoever takes it the `survey.json` and the gate report: the credentials,
the hosts, the disk figure and the swallowed install steps are exactly what
the next stage would otherwise rediscover.

## 1. The repository's own instructions — always first

If `survey.py` reported `handoff.repo-ships-guidance`, use those. A
project-local `SKILL.md`, `AGENTS.md` or `CLAUDE.md` was written by someone
who knows which config selects which checkpoint and what the CLI actually
wraps. No general-purpose survey competes with that.

`Proteina-Complexa` is the current high-water mark: five skills under
`.claude/skills/`, a host probe, and a run manifest that pins the git SHA and
checkpoint hashes. Nothing here duplicates them.

## 2. `fcakyon/phd-skills` — reimplementing a paper

[github.com/fcakyon/phd-skills](https://github.com/fcakyon/phd-skills) — MIT.

Its `reproduce` skill walks seven stages: paper acquisition, code inventory,
**gap analysis**, implementation, dataset acquisition, smoke runs, replication.
Two parts are worth reaching for by name:

- **`references/03-gap-analysis.md`** — extracting every hyperparameter the
  paper implies but does not state, each tagged with its provenance
  (`[paper §4.1]`, `[code:path:line]`, `[guess]`), with a rule that more than
  30% guesses means the paper is under-specified and the reproduction will be
  approximate.
- **`references/06-smoke.md`** — three gated tiers, forward pass → single
  optimiser step → 20 iterations, each with pass criteria and a list of causes
  when it fails.

**Where it fits:** the paper ships no code, or partial code, and you intend to
write the missing training loop.

**Where it does not:** its worldview is supervised training — optimiser,
batch size, augmentation order, loss coefficients, `initial loss ≈ ln(1000)`.
Its dataset stage suggests substituting a similar public dataset when the
original is private, which is reasonable for ImageNet-like work and
meaningless when the dataset *is* a specific list of PDB accessions. For a
structural-biology repository where you are running released weights, most of
it does not apply.

## 3. `lllllllama/RigorPilot-Skills` — running an existing repository

[github.com/lllllllama/RigorPilot-Skills](https://github.com/lllllllama/RigorPilot-Skills) — MIT.

A research-workflow framework whose `ai-research-reproduction` entry point is
README-first: it works through the repository's documented commands and emits
an annotated copy of the README with per-section results, plus a
`REPRODUCIBILITY_NOTES.md` recording commands, configs, seeds, checkpoints and
known gaps.

**Where it fits:** the repository works and is documented, and you want the
documented path executed and recorded.

**Where it does not:** like `phd-skills` it is aimed at deep-learning
experiments generally, and says nothing about licence layers or access gates.

```bash
npx skills add lllllllama/rigorpilot-skills --skill ai-research-reproduction
```

## 4. `bytedance/Repo2Run` — when the environment is the problem

[github.com/bytedance/Repo2Run](https://github.com/bytedance/Repo2Run) — an
agent that generates a working Dockerfile for a Python repository by
iterating until the install succeeds. Not a skill; research code, and the
licence is worth checking before use.

**Where it fits:** the gate says `degraded` for environment reasons — swallowed
install failures, a fix only in prose, a manifest that does not describe the
environment — and you would rather have a reproducible container than debug
someone's shell script.

## A note on what you are handing to

[`lcrawfurd/claude-skills`](https://github.com/lcrawfurd/claude-skills)
implements a five-way computational reproducibility audit aimed at economics
and the social sciences. It is a good design and it is worth reading. It also
ships **no licence file**, which by default means all rights reserved.

That is the `license.absent` finding, in the tooling rather than in the
science, and it applies to skills as much as to models: read the licence of
what you adopt.

## The sibling skills here

- **`paper-deep-reading`** — whether the claims were supported in the first
  place. Worth running *before* a reproduction: a claim with no evidence
  behind it in the paper is not made true by rerunning the code.
- **`literature-tracking`** — finding the paper, and the preprint it became.

## What to hand over

```
survey.json        the repository's demands, with file:line evidence
probe.json         the host, credentials as present/absent only
REPRODUCTION.md    the gate report
```

`survey.json` is host-independent, so gating the same repository against a
different machine means re-running `probe.py` and `gate.py` there and nothing
else. Keep all three next to the run: they are the record of what was known
before it started, which is the part nobody writes down and everybody wants
six months later.

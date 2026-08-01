# bio-research-skills

[![CI](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/ci.yml)
[![Live upstream checks](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/live.yml/badge.svg)](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/live.yml)

Agent Skills for the daily grind of life-science research — **track** the
literature, **read** what matters, **reproduce** the code.

Not a chatbot you query. Skills that take a task and do it.

## Install

Requires Node.js. Works with Claude Code, Cursor, Codex and 40+ other agents via
the [`skills` CLI](https://github.com/vercel-labs/skills).

```bash
npx skills add Jinsong-Zhou/bio-research-skills                              # all skills, this project
npx skills add Jinsong-Zhou/bio-research-skills -g                           # globally
npx skills add Jinsong-Zhou/bio-research-skills --skill literature-tracking  # just one
```

Nothing needs a package installed or a credential configured — the bundled
scripts are standard-library Python 3.9+, and CI runs the suite on 3.9 and 3.13.
Set `BIO_RESEARCH_CONTACT` to your email so Crossref and NCBI can identify you.

`paper-deep-reading` delivers a `.docx` through the `docx` skill from
[anthropics/skills](https://github.com/anthropics/skills) — **licensed
separately**, © Anthropic, needs Node.js, not vendored here. Install it with
`/plugin marketplace add anthropics/skills` then
`/plugin install document-skills@anthropic-agent-skills`. Without it the note
still renders to Markdown, and says why.

## Skills

### 📡 [`literature-tracking`](skills/literature-tracking/) · v0.1

*Three disconnected firehoses — arXiv q-bio, bioRxiv/medRxiv, PubMed. The same
paper three times; alerts too broad or too narrow.*

- Queries every source in one pass and hands back one ranked, deduplicated set.
- **Merges preprints with the journal versions they became** — the part nobody
  else does. Four rules, cheapest first: identical DOI, bioRxiv's `published`
  field, a guarded title fingerprint, Crossref's preprint relations.
- Refuses to fail silently. These APIs answer **HTTP 200** to a broken query:
  bioRxiv ignores an unknown subject area and returns everything, Europe PMC
  drops clauses it cannot parse, PubMed dates year-only records to 1 January.
  Each has a guard and measured evidence in
  [`references/source-quirks.md`](skills/literature-tracking/references/source-quirks.md).

### 📖 [`paper-deep-reading`](skills/paper-deep-reading/) · v0.1

*One paper, read properly. Anything can summarise; knowing whether the
conclusions actually hold is the work, and it never gets written down.*

- Takes a PDF, an arXiv id, a DOI or a preprint link; returns a Word document in
  two halves — what the paper does, then whether it holds up.
- The first half **teaches**: the problem *and why it is hard*, the idea mapped
  obstacle by obstacle, the pipeline step by step, the mechanism and where it
  stops working.
- The second half is a table, not prose. Every claim names the figure, table or
  section that backs it; a claim backed by nothing is recorded as such, and
  validation will not accept "the authors state that…" as evidence.
- Leads with the checks that have no equivalent outside biology — correlation
  presented as mechanism, model-system distance, proxy versus target, train/test
  homology leakage
  ([`references/credibility-checks.md`](skills/paper-deep-reading/references/credibility-checks.md)).

### 🔬 [`code-reproduction`](skills/code-reproduction/) · v0.1

*A GitHub link ≠ the paper's numbers: dependency drift, CUDA mismatches, missing
weights, an afternoon lost.*

- Answers one question — **should you start?** — before the afternoon is spent.
  It does not run the model; when the answer is yes it hands over to a skill
  that does.
- Defaults to **inference**, training opt-in. The good reproduction skills all
  assume you are retraining; in structural biology nobody is.
- Reads licences in four layers — code, weights, data, third-party — because a
  repository's `LICENSE` file is not its licence. Across three consecutive
  papers from one lab the terms reverse, and GitHub's own detector is wrong
  about all three.
- Verdicts are `blocked`, `unknown`, `degraded`, `ok`, and **`unknown` ranks
  worse than `degraded`** on purpose: a report that reads clear because a check
  quietly found nothing is the failure this skill exists to prevent.

## Acknowledgements

- **[openags/paper-search-mcp](https://github.com/openags/paper-search-mcp)** (MIT)
  — the `Paper` record schema mirrors its field names; its `_paper_unique_key`
  is the baseline dedup rule 1 reimplements. Still the better tool for ad-hoc
  keyword search across 20+ sources.
- **[RainerSeventeen/paper-tracker](https://github.com/RainerSeventeen/paper-tracker)** (MIT)
  — dedup rule 3 follows the approach in its `core/dedup.py`.
- **[Scholar Inbox](https://arxiv.org/abs/2504.08385)** (Krishnan et al., ACL 2025 demo)
  — the argument for a *calibrated* relevance score you can threshold rather
  than a fixed top-N.
- **[TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)** (AGPL-3.0)
  — studied for its recency-weighted profile ranking. **Ideas only; no code
  copied or adapted**, since AGPL is incompatible with this repository's MIT.
- **[anthropics/skills](https://github.com/anthropics/skills)** (proprietary, © Anthropic)
  — Word and slide rendering is delegated to its `docx` and `pptx` skills. No
  code copied; they are prerequisites and their terms are yours to accept.
- **[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)** (MIT)
  — documenting each API's silent-failure modes in per-source `references/`
  files is their practice, and a good one.
- **[fcakyon/phd-skills](https://github.com/fcakyon/phd-skills)** (MIT) —
  `code-reproduction` deliberately stops where its `reproduce` skill starts, and
  points at it. **Ideas and routing only; no code copied.**
- **[lllllllama/RigorPilot-Skills](https://github.com/lllllllama/RigorPilot-Skills)** (MIT)
  — the other handoff target, for README-first reproduction of a repository that
  already works. Named as a prerequisite, not vendored.
- **[NVIDIA-BioNeMo/Proteina-Complexa](https://github.com/NVIDIA-BioNeMo/Proteina-Complexa)**
  (Apache-2.0 code, NVIDIA Open Model License weights) — `probe.py` follows the
  shape of its `preflight.sh`, and it is the worked example throughout
  `references/repro-hazards.md`. **No code copied.**

## License

MIT — see [LICENSE](LICENSE).

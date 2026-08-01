# bio-research-skills

[![CI](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/ci.yml)
[![Live upstream checks](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/live.yml/badge.svg)](https://github.com/Jinsong-Zhou/bio-research-skills/actions/workflows/live.yml)

Agent Skills for the daily grind of life-science research — **track** the literature,
**read** what matters, **reproduce** the code.

Not a chatbot you query. Skills that take a task and do it.

```bash
npx skills add Jinsong-Zhou/bio-research-skills
```

---

## The three skills

| Skill | What it lifts off your plate | Status |
|---|---|---|
| [`literature-tracking`](skills/literature-tracking/) | Three disconnected firehoses — arXiv q-bio, bioRxiv/medRxiv, PubMed. The same paper three times; alerts too broad or too narrow. | ✅ v0.1 |
| [`paper-deep-reading`](skills/paper-deep-reading/) | One paper, read properly. Anything can summarise; knowing whether the conclusions actually hold is the work, and it is the part that never gets written down. | ✅ v0.1 |
| [`code-reproduction`](skills/code-reproduction/) | A GitHub link ≠ the paper's numbers: dependency drift, CUDA mismatches, missing weights, an afternoon lost. | ✅ v0.1 |

### `literature-tracking`

Queries all three sources in one pass, merges preprints with the journal
versions they became, and hands the agent a ranked, deduplicated set.

The part nobody else does is **preprint → published merging**. Every tool we
surveyed keys on an exact DOI or an exact title string, so a bioRxiv preprint
and its journal article stay two separate entries. Four rules fix that, cheapest
first: identical DOI, bioRxiv's own `published` field, a guarded title
fingerprint, then Crossref's `is-preprint-of`/`has-preprint` relations.

Dedup also does double duty as a relevance signal. bioRxiv has no keyword
search — you can filter by subject area and nothing else — so Europe PMC runs
as a second, keyword-searchable view of the same preprints. Measured coverage
ran 43–78% across five days (66% overall) with about a day of indexing lag, and
the newest day — the one a tracking query is for — is the worst covered. So it
supplements rather than replaces the direct
sweep; but the two views share DOIs, so they merge, and whatever came through
the keyword channel is flagged `keyword_match`. Nothing is dropped, and the
agent knows where to start reading.

It also refuses to fail silently. Each of these returns **HTTP 200**:

- bioRxiv **ignores an unknown subject area** and returns every paper in the
  window — real papers, real DOIs, entirely unrelated to what you asked
- bioRxiv also pages at 30, not the 100 its cursor implies, **oldest first** —
  so a naive read returns the stalest slice of the window
- PubMed's `<PubDate>` is often year-only, putting every record on 1 January
- PubMed bounds the search on the *Entrez* date but reports the *publication*
  date, so a 7-day window legitimately returns papers months old
- Europe PMC drops query clauses it cannot parse and answers 200 anyway
- Europe PMC also leaves inline HTML in titles (`peptidyl-prolyl
  <i>cis-trans</i> …`), which quietly stops them matching the same paper
  fetched from bioRxiv
- arXiv answers a query with an unknown field prefix with 0 results — the same
  answer as a quiet week. (It used to answer a malformed *structured* query
  with a one-entry feed titled `Error`; as of 2026-07-30 that one is an
  HTTP 400, which the live tests caught.)

Each has a guard, each is documented with measured evidence in
[`references/source-quirks.md`](skills/literature-tracking/references/source-quirks.md).

### `paper-deep-reading`

Takes one paper — a PDF, an arXiv id, a DOI, a preprint link — and produces a
Word document in two halves: what the paper does, then whether it holds up.

The first half has to **teach**, not summarise. Five fields, each answering a
question the one before it raises: what the problem is *and why it is hard*;
the idea, mapped obstacle by obstacle onto that problem; what it concretely
does, step by step; the mechanism that carries the result and where it stops
working; then what came out. How the pipeline decomposes depends on what kind
of paper it is — a model has training and inference, a cryo-EM structure has
sample prep and reconstruction — so the type is chosen during the reading pass
rather than assumed.

The second half is where the value is. A summary restates the abstract; an
assessment asks where each claim is actually supported, and the interesting
rows are the ones where the answer is *nowhere*. So the note is structured
rather than prose: every claim gets a row naming the figure, table or section
that backs it, and a claim with no such pointer has to be recorded as having
none. Validation enforces that whenever the full text was available — it cannot
tell whether `Table 2` is the *right* table, but it will not let a claim through
with "the authors state that…" in the evidence column.

Reading order is part of the skill, not an afterthought. Figures before the
prose that interprets them, methods held to the single question of how those
numbers were produced, related work last — once you know what the paper did and
can judge whether it is compared against the right things.

[`references/credibility-checks.md`](skills/paper-deep-reading/references/credibility-checks.md)
carries the checklist. It leads with the three checks that have no equivalent
outside biology: **correlation presented as mechanism** (a knockdown without a
rescue is one experiment short of causal), **the model system and its distance
to the claim** (HEK293 is not a neuron, overexpression is itself a
perturbation), and **the proxy versus the target** (mRNA is not protein,
colocalisation is not interaction). For computational work, train/test homology
leakage is the most common failure and the least often disclosed.

It also refuses to pretend it has the paper. Paywalled articles with no
open-access version are reported as such, with the abstract, and the document
says on its first line that it was written without the full text — because an
assessment built on an abstract is auditing the half that contains no evidence.

### `code-reproduction`

Takes the repository a paper points at and answers one question — **should you
start?** — before the afternoon is spent. It does not run the model; when the
answer is yes it hands over to a skill that does.

The reason it exists alongside the good reproduction skills that already
ship is that they all assume you are **retraining**. Their gap analysis is
optimiser, batch size, augmentation order, loss coefficients; their smoke test
checks that the initial loss is near `ln(1000)`; their advice when a dataset
is private is to substitute a similar public one. That is the right shape for
computer vision. In structural biology nobody retrains — you run released
weights — and "substitute a similar dataset" is meaningless when the dataset
*is* a list of 45,856 PDB accessions. So the default target here is
**inference**, and training is opt-in.

The obstacles that actually stop you are obstacles of **access**: a checkpoint
behind a login, a disk figure quoted for outputs that nobody budgeted for, a
CUDA build the manifest never mentions, Ubuntu 22.04 or a GLIBC error, and a
licence on the weights that is not the licence on the code.

That last one is the part no other tool checks, and it is the only failure
that arrives *after* the work succeeds. A repository's `LICENSE` file is not
its licence. Three consecutive papers from one lab, verified 2026-07-31:

| | `proteina` | `la-proteina` | `Proteina-Complexa` |
|---|---|---|---|
| Layout | one `LICENSE` file | `LICENSE/` **directory** | `LICENSE` pointer + `licenses/` |
| Code | NVIDIA License | Apache-2.0 | Apache-2.0 |
| Weights | *same file* | NVIDIA **Open Model** License | NVIDIA **Open Model** License |
| Commercial use | **no** | yes | yes |
| GitHub's detector | `NOASSERTION` | **nothing** | `NOASSERTION` |

The terms reverse between the first two, the two NVIDIA licences differ by one
word, and GitHub's own detector is wrong about all three — for `la-proteina`
because `LICENSE` there is a directory, which no root-file scanner can read.
So the four layers — code, weights, data, third-party — are read separately,
and every licence family in a file is collected rather than the first.

It also hunts the silent failures, which are the same defect class as the
HTTP 200s in `literature-tracking`. `env/build_uv_env.sh` in Proteina-Complexa
ends two install steps with `|| echo "Warning: …"`: the build prints
"Installation Complete!", the package is absent, and the run dies hundreds of
steps later inside an unrelated import. For one of those two the README
documents the cause and gives the fix — inside a `>` callout, and only in the
README; the script as shipped does not contain it, and the workaround itself
ends in `|| true`.

Verdicts are `blocked`, `unknown`, `degraded`, `ok`, and **`unknown` ranks
worse than `degraded`** on purpose. A stated 40 GB requirement against a card
nobody probed is not a pass, and the failure this skill exists to prevent is a
report that reads clear because a check quietly found nothing. Whatever the
survey could not settle reaches the verdict rather than an appendix.

When a repository ships its own agent instructions — Proteina-Complexa ships
five — the skill says so and defers to them for how to run the pipeline,
covering only what such files never do.

---

## Install

Requires Node.js. Works with Claude Code, Cursor, Codex, and 40+ other agents via the
[`skills` CLI](https://github.com/vercel-labs/skills).

```bash
# all skills, into the current project
npx skills add Jinsong-Zhou/bio-research-skills

# a single skill
npx skills add Jinsong-Zhou/bio-research-skills --skill literature-tracking

# globally, for every project
npx skills add Jinsong-Zhou/bio-research-skills -g

# non-interactive
npx skills add Jinsong-Zhou/bio-research-skills --all -y
```

Nothing here needs a package installed or a credential configured. The bundled
scripts are standard-library Python 3.9+ — CI runs the whole suite on 3.9 and
3.13, because a version floor nobody tests is a version floor nobody keeps.

### Word and slide output

`paper-deep-reading` delivers a `.docx`, and it does not write one itself — it
hands the note to the [`docx` skill](https://github.com/anthropics/skills) from
Anthropic's collection:

```
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills
```

That skill is **licensed separately** — © Anthropic, with terms running through
your agreement with Anthropic — and needs Node.js plus the
[`docx`](https://www.npmjs.com/package/docx) npm package, which is MIT and a
separate thing. Neither is vendored here. Outside a Claude environment, check
those terms before relying on it.

What gets handed over is `note.py render --format blocks` — a typed document
tree with the headings already in the note's language — not the Markdown. A
renderer should not have to parse a table back out of pipe characters.

Without the skill, `paper-deep-reading` renders the same note to Markdown and
says why. Slides work the same way, from the same blocks, via `pptx`.

---

## Layout

```
skills/
  literature-tracking/
    SKILL.md          # the skill itself
    scripts/          # bundled tooling
    references/       # per-API notes, loaded on demand
    tests/
  paper-deep-reading/
    ...               # same shape
  code-reproduction/
    ...               # same shape
```

Each skill folder carries a `SKILL.md` with YAML frontmatter (`name`, `description`).
Skills are self-contained: no shared runtime, install one without the others.

---

## Design principles

1. **Wrap what exists, build only what doesn't.** The ecosystem already has good
   fetchers, parsers, and agent loops. What it lacks is the connective tissue.
2. **Provenance or it didn't happen.** Every claim traces to a source — a DOI, a page
   number, a log line.
3. **Fail loudly.** Several literature APIs return HTTP 200 on error. Verify response
   structure, never the status code alone.
4. **Local-first where it matters.** Unpublished lab data stays local.
5. **Permissive licenses in, licences named out.** Code shipped in this
   repository is MIT or compatible — no AGPL, no research-only weights, no
   unlicensed code. External tools we *recommend* are a separate question:
   they are named with their licence so you can decide for yourself. The `docx`
   skill is the current example — proprietary, useful, and not vendored.

---

## Development

```bash
uv sync
uv run pytest -m "not live"   # offline suite
uv run pytest -m live         # re-checks that the documented API quirks are still real
uv run ruff check .
```

The `live` tests are the interesting ones: they assert that bioRxiv *still*
ignores unknown categories, that arXiv *still* accepts structured queries, and
that preprint DOIs *still* come with the prefixes we route. When upstream
changes something, those tests fail and say which guard needs a look.

They run on a **Monday schedule**, not on pull requests, and a failure opens an
issue. Both halves are deliberate: a third party's outage should not redden
somebody's PR, and a scheduled job that fails where nobody looks is the same
silent failure this repository is about.

## Acknowledgements

- **[openags/paper-search-mcp](https://github.com/openags/paper-search-mcp)**
  (MIT) — the `Paper` record schema here mirrors its field names so records stay
  interchangeable, and its `_paper_unique_key` is the baseline our dedup rule 1
  reimplements. It remains the better tool for ad-hoc keyword search across 20+
  sources; this skill is narrower on purpose, covering the date-windowed
  tracking case its API surface does not expose.
- **[RainerSeventeen/paper-tracker](https://github.com/RainerSeventeen/paper-tracker)**
  (MIT) — dedup rule 3 follows the approach in its `core/dedup.py`: normalised
  DOI, title + first-author + year fingerprint, minimum-length guard, and
  source-rank primary selection.
- **[Scholar Inbox](https://arxiv.org/abs/2504.08385)** (Krishnan et al.,
  ACL 2025 demo) — the argument for a *calibrated* relevance score you can
  threshold, rather than a fixed top-N, comes from their per-user ranking work.
- **[TideDra/zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily)**
  (AGPL-3.0) — studied for its recency-weighted profile ranking. **Ideas only;
  no code was copied or adapted**, since AGPL is incompatible with this
  repository's MIT license.
- **[anthropics/skills](https://github.com/anthropics/skills)** (proprietary,
  © Anthropic) — `paper-deep-reading` delegates Word and slide rendering to the
  `docx` and `pptx` skills there rather than reimplementing either. No code is
  copied; they are named as prerequisites and their terms are the user's to
  accept. See the Install section.
- **[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)**
  (MIT) — the practice of documenting each API's silent-failure modes in
  per-source `references/` files is theirs, and it is a good one.
- **[fcakyon/phd-skills](https://github.com/fcakyon/phd-skills)** (MIT) —
  `code-reproduction` deliberately stops where its `reproduce` skill starts,
  and points at it. Its provenance-tagged gap analysis and three-tier smoke
  test are better than anything a feasibility check should be growing, and
  reimplementing them here would have been the wrong kind of thorough. **Ideas
  and routing only; no code copied.**
- **[lllllllama/RigorPilot-Skills](https://github.com/lllllllama/RigorPilot-Skills)**
  (MIT) — the other handoff target, for README-first reproduction of a
  repository that already works. Named as a prerequisite, not vendored.
- **[NVIDIA-BioNeMo/Proteina-Complexa](https://github.com/NVIDIA-BioNeMo/Proteina-Complexa)**
  (Apache-2.0 code, NVIDIA Open Model License weights) — the shape of
  `probe.py`, one JSON snapshot per host with every field degrading rather
  than failing, follows its `.claude/skills/_shared/scripts/preflight.sh`. **No
  code copied.** It is also the worked example throughout
  `references/repro-hazards.md`: every hazard cited there is a real,
  reproducible observation about a well-maintained public repository, and
  eight of them are re-checked against the live repository by
  `tests/test_live_upstream.py`, so those citations cannot rot unnoticed.

## License

MIT — see [LICENSE](LICENSE).

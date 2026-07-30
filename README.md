# bio-research-skills

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
| `paper-deep-reading` | Triage dozens a week, deep-read a few — most turn out "meh". Notes never get written; group slides cost a late night. | 📋 planned |
| `code-reproduction` | A GitHub link ≠ the paper's numbers: dependency drift, CUDA mismatches, missing weights, an afternoon lost. | 📋 planned |

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

---

## Layout

```
skills/
  literature-tracking/
    SKILL.md          # the skill itself
    scripts/          # bundled tooling
    references/       # per-API notes, loaded on demand
    tests/
```

`paper-deep-reading/` and `code-reproduction/` will sit alongside it; neither
exists yet.

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
5. **Permissive licenses only.** No AGPL, no research-only weights, no unlicensed code.

---

## Development

```bash
uv sync
uv run pytest -m "not live"   # offline suite
uv run pytest -m live         # re-checks that the documented API quirks are still real
uv run ruff check .
```

The `live` tests are the interesting ones: they assert that bioRxiv *still*
ignores unknown categories and that arXiv *still* accepts structured queries.
When upstream fixes something, those tests fail and tell us a guard can go.

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
- **[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)**
  (MIT) — the practice of documenting each API's silent-failure modes in
  per-source `references/` files is theirs, and it is a good one.

## License

MIT — see [LICENSE](LICENSE).

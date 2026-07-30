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
| `literature-tracking` | Three disconnected firehoses — arXiv q-bio, bioRxiv/medRxiv, PubMed. The same paper three times; alerts too broad or too narrow. | 🚧 in design |
| `paper-deep-reading` | Triage dozens a week, deep-read a few — most turn out "meh". Notes never get written; group slides cost a late night. | 📋 planned |
| `code-reproduction` | A GitHub link ≠ the paper's numbers: dependency drift, CUDA mismatches, missing weights, an afternoon lost. | 📋 planned |

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
  paper-deep-reading/
  code-reproduction/
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
5. **Permissive licenses only.** No AGPL, no research-only weights, no unlicensed code.

---

## License

MIT — see [LICENSE](LICENSE).

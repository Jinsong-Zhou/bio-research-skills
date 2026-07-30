---
name: literature-tracking
description: Track new life-science papers across arXiv q-bio, bioRxiv, medRxiv and PubMed in one pass, merging preprints with their published journal versions so nothing appears twice, then rank what is left against the user's research interests. Use when asked what is new in a field, for a literature digest or weekly roundup, to catch up after time away, to monitor a topic or set of authors, or whenever a query spans more than one preprint server or literature database. Triggers on "anything new on X", "papers this week", "literature digest", "what did I miss", "keep me updated on", "recent preprints", or any mention of arXiv q-bio, bioRxiv, medRxiv or PubMed together.
license: MIT
allowed-tools: Bash Read
compatibility: Needs network access and Python 3.9+. Scripts use only the standard library — no packages to install, no credentials required. Set NCBI_API_KEY to raise PubMed's rate limit from 3 to 10 requests/second, and BIO_RESEARCH_CONTACT to your email so Crossref and NCBI can identify you.
metadata:
  version: "0.1"
  skill-author: "Jinsong Zhou"
---

# Literature tracking

Three literature firehoses, one deduplicated stream, ranked against what the
user actually works on.

`scripts/track.py` does the deterministic half — query, normalise, deduplicate.
**You** do the judgement half: deciding what is worth their attention and
saying why. Do not try to script that; you are better at it than a keyword
filter, and you can explain yourself.

## Workflow

### 1. Establish the research profile

You need topics before you can rank. Take them from the conversation, from
project memory, or ask. A usable profile has:

- **topics** — what they study, in their words ("cryo-EM of membrane
  transporters", not "biology")
- **methods or systems** — techniques and target molecules that matter
- **exclusions** — the neighbouring work they keep getting served and do not want

If they have named seed papers, treat those as the strongest signal available.

### 2. Choose the window and sources

Default to the last 7 days across arXiv, bioRxiv and PubMed. Widen to `30d` if
the topic is slow-moving or the last check was long ago. Add `medrxiv` only for
clinical questions.

### 3. Map the profile onto each source — they do not accept the same query

This is the step that goes wrong. The three sources have **incompatible query
models**, and a query that suits one silently misbehaves on another:

| Source | Keyword search | Date window | Subject filter |
|---|---|---|---|
| arXiv | yes | yes | `cat:q-bio.*` |
| bioRxiv / medRxiv | **none** | yes | subject area, **validated** |
| PubMed | yes, with field tags | yes | via MeSH in the term |

So: send **keywords** to arXiv and PubMed, send **subject areas** to
bioRxiv/medRxiv, and let the ranking in step 6 do the narrowing there.

Pick bioRxiv areas from the profile, not from habit. `biochemistry`,
`biophysics` and `molecular biology` suit structural work; add
`synthetic biology` and `bioengineering` whenever protein *design* is in scope —
leaving them out is how a design-focused digest ends up with no design papers.
`bioinformatics` is high-volume and low-yield for a structural lab (tool and
pipeline announcements); include it only if methods development is the point.

Passing a keyword where bioRxiv expects a subject area is the single most
dangerous mistake available here — the API drops the filter and returns
unrelated papers that look completely legitimate. The script refuses to do it,
and will suggest the closest real subject area. Read
`references/biorxiv-categories.md` when picking areas.

### 4. Run the fetch

**Run from the skill's own directory** — the scripts import each other by bare
name, so `scripts/` has to be the working root:

```bash
cd "$(dirname "$0")"   # i.e. the directory holding this SKILL.md
python3 scripts/track.py \
  --since 7d \
  --keywords "cryo-EM" "membrane transporter" "structure prediction" \
  --biorxiv-categories biochemistry biophysics "molecular biology" \
  --sources arxiv biorxiv pubmed \
  --max-per-source 200 \
  > /tmp/papers.json
```

Write the JSON outside the skill directory so runs never dirty the repo.

Useful flags: `--until` for a closed window, `--pubmed-term` for a raw PubMed
query with field tags, `--medrxiv-categories` for clinical work,
`--max-crossref-lookups` to raise the dedup tier-2 budget, `--no-crossref` to
skip that tier entirely. `--help` lists everything.

Sizing: `--max-per-source 200` over 7 days is a good starting point. It is
**split evenly across bioRxiv subject areas**, so four areas get 50 each — if
you want depth in one area, ask for fewer areas rather than a bigger number.
Expect a few minutes: arXiv permits one request per 3 seconds and the script
honours it.

A whole run takes minutes, not seconds, and prints progress to stderr. Let it
finish; a killed run leaves you with a truncated JSON file that still parses.

### 5. Check `stats` and `errors` before reading a single paper

```jsonc
"stats": {
  "fetched_by_source": {"arxiv": 43, "biorxiv": 118, "pubmed": 200},
  "duplicates_merged": 12,
  "merges_by_tier": {"biorxiv-published": 9, "crossref-relation": 3},
  "crossref_skipped": 0
}
```

- **`errors` non-empty** — say which source failed and that the digest is
  partial. Never present an incomplete sweep as a complete one.
- **`fetched_by_source` at exactly `max_per_source`** — that source was
  truncated. Narrow the query or raise the cap; do not silently report the
  first 200.
- **`crossref_skipped` above zero** — the tier-2 budget ran out, so some
  preprint/journal pairs may still be listed twice. Re-run with a higher
  `--max-crossref-lookups`, or say so in the digest.

PubMed needs one more caution. Its window is bounded on the **Entrez date**
(when PubMed indexed the record), not the publication date, so a 7-day sweep
legitimately surfaces papers published months earlier. `published_date` is when
it was published; `extra.entrez_date` is what the search filtered on. Do not
present an April paper under a "this week" heading without saying which is which.

### 6. Rank against the profile — this is your job

A 200-paper report is roughly 250 KB; do not read it whole. Extract titles
first, shortlist on those, then pull abstracts only for the shortlist:

```bash
python3 -c "import json;[print(i,p['source'],p['title']) for i,p in enumerate(json.load(open('/tmp/papers.json'))['papers'])]"
```

Then judge each shortlisted paper on title and abstract:

- **score 0–5** for relevance to the profile
- **one sentence** on why it matters to *this* user — not a summary of the
  abstract, a reason to care

Calibrate the scale so runs stay comparable:

| Score | Meaning |
|---|---|
| **5** | Changes what they do next — their system, their method, a result that invalidates an assumption they hold |
| **4** | Squarely on-topic; they would want to read it this week |
| **3** | Adjacent and useful — a review, a tool, a neighbouring system |
| **2** | Same field, no bearing on their work |
| **0–1** | Matched a keyword by accident, or hits a stated exclusion |

Keep what clears a threshold (3 is a reasonable default) rather than a fixed
top-N. A quiet week should produce a short digest; forcing ten items means
padding with noise. That is the "alerts too broad or too narrow" problem, and a
fixed N recreates it.

Weigh: overlap with stated topics; whether the method is one they use; whether
it challenges or extends work they follow; whether an author is someone they
track. Down-weight anything matching their exclusions.

### 7. Present the digest

Group by theme, not by source — the user does not care which pipe it came out
of. Per paper: title, authors (first + et al.), date, source, one-line reason,
link. Lead with the two or three that genuinely matter.

When a paper carries `also_in`, it was published *and* preprinted. Cite the
version of record and mention the preprint is available — that is often the
one they can actually read.

Close with what was searched: window, sources, how many were scanned, how many
made the cut. Trust comes from showing the funnel.

## What the script guarantees, and what it does not

**Guarantees.** Structured arXiv queries that actually filter. Validated
bioRxiv subject areas. Day-precision PubMed dates. Real pagination. Preprint
and journal versions merged via four tiers, with the reason recorded in
`merge_reason`.

**Does not.** Judge relevance, remember previous runs, or schedule itself.
Ranking is step 6. Persistence and scheduling belong to the agent framework —
if the user wants a daily digest, wire it up there and call this each time.

## Traps

Before modifying anything under `scripts/sources/`, read
`references/source-quirks.md`. Every failure mode documented there returns
**HTTP 200** — arXiv's error document, bioRxiv's ignored category filter,
PubMed's year-only dates. A status-code check catches none of them.

Two habits worth keeping:

- bioRxiv has **no keyword search**. If asked to keyword-filter it, explain
  that upstream does not support it and that ranking handles it instead.
- Never guess a subject area. The whitelist is in
  `references/biorxiv-categories.md` and the script suggests near matches.

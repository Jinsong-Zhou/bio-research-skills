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
| Europe PMC | yes — over the same preprints | yes | by publisher |

So: send **keywords** to arXiv, PubMed and Europe PMC; send **subject areas**
to bioRxiv/medRxiv; let the ranking in step 6 narrow the rest.

**Europe PMC is the keyword channel onto the preprint servers.** bioRxiv has no
keyword search, so a subject-area sweep is mostly noise; Europe PMC indexes the
same preprints and *does* search full text. It only covers ~70% of them and
lags a day, so it supplements the direct fetch rather than replacing it — but
records carry the same DOI, so the two views merge, and anything reached
through the keyword channel comes out flagged `extra.keyword_match`. Use that
flag to decide what to read first; it never removes anything from the list.

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

**Run from the directory holding this `SKILL.md`** — the scripts import each
other by bare name, so `scripts/` has to be the working root. Substitute the
real path; there is no `$0` here, this file is not a script.

```bash
cd /path/to/skills/literature-tracking
python3 scripts/track.py \
  --since 7d \
  --keywords "cryo-EM" "membrane transporter" "structure prediction" \
  --biorxiv-categories biochemistry biophysics "molecular biology" "synthetic biology" \
  --max-per-source 500 \
  > /tmp/papers.json
```

Write the JSON outside the skill directory so runs never dirty the repo.

Useful flags: `--until` for a closed window, `--pubmed-term` for a raw PubMed
query with field tags, `--medrxiv-categories` for clinical work, `--crossref
on|off` to override the Crossref dedup rule. `--help` lists everything.

That rule is off by default for short windows, and the run says so. It merges a
preprint with the journal article it became — which only helps if both fall
inside the same query, and they are usually months apart. Over a measured
7-day window it cost 60 lookups and merged nothing. Turn it on for retrospective
sweeps of 60+ days, where both versions can genuinely co-occur.

Sizing: `--max-per-source 500` over 7 days. It is **split evenly across bioRxiv
subject areas**, so four areas get 125 each — for depth in one area, ask for
fewer areas rather than a bigger number. PubMed needs the headroom most: a
broad keyword set easily matches 400+ records a week, and a cap below that does
not sample the window (see step 5). Expect a few minutes; arXiv permits one
request per 3 seconds and the script honours it.

Keywords reach PubMed and Europe PMC, **not arXiv**. All of q-bio runs under a
hundred submissions a week — small enough to rank by hand — and ANDing keywords
onto it cut a measured window from 79 papers to 1, partly because arXiv splits
hyphenated terms even inside quotes. `--arxiv-keywords` exists if the category
filter really is too broad, but reach for `--arxiv-categories` first.

Choose PubMed keywords with care too. Generic method words match far beyond
biology: `"molecular dynamics"` and `"binding affinity"` pull in materials
science, cement chemistry and battery papers, which can be most of what comes
back. Pair them with a subject term through `--pubmed-term`, e.g.
`'("molecular dynamics"[TIAB] AND (protein[TIAB] OR membrane[TIAB]))'`.

A whole run takes minutes, not seconds, and prints progress to stderr. Let it
finish; a killed run leaves you with a truncated JSON file that still parses.

### 5. Check `stats` and `errors` before reading a single paper

```jsonc
"stats": {
  "coverage_by_source": {
    "pubmed": {"fetched": 200, "available": 556, "truncated": true,
               "covers": ["2026-07-28", "2026-07-29"]}
  },
  "truncated_sources": ["pubmed"],
  "duplicates_merged": 20,
  "merges_by_tier": {"exact-doi": 19, "title-fingerprint": 1},
  "rule_matches": {"exact-doi": 19, "title-fingerprint": 18}
}
```

- **`errors` non-empty** — say which source failed and that the digest is
  partial. Never present an incomplete sweep as a complete one.
- **`truncated_sources` non-empty** — ⚠️ **this is not a random sample.** Every
  one of these APIs returns its newest records first, so a truncated fetch
  drops the *early days of the window entirely*. Check `covers`: in the example
  above a nominal 7-day PubMed sweep actually reached two days. Re-run with a
  higher `--max-per-source` before writing anything, or the digest silently
  describes the wrong week — and cross-source dedup misses pairs whose other
  half fell in the discarded days.
- **`crossref_skipped` above zero on a short window** — expected, and not worth
  fixing. The rule is off under 60 days precisely because it cannot pay off
  there; raising `--max-crossref-lookups` buys minutes of requests for nothing.
  Only raise it on a 60+ day sweep.
- **`merges_by_tier` vs `rule_matches`** — the first counts new merges, the
  second counts every rule agreement. A rule showing 0 merges but many matches
  is working; it just agreed with a cheaper rule that got there first. Judge a
  rule by `rule_matches`, and individual papers by their `merge_reason`.

PubMed needs one more caution. Its window is bounded on the **Entrez date**
(when PubMed indexed the record), not the publication date, so a 7-day sweep
legitimately surfaces papers published months earlier. `published_date` is when
it was published; `extra.entrez_date` is what the search filtered on. Do not
present an April paper under a "this week" heading without saying which is which.

### 6. Rank against the profile — this is your job

A 200-paper report is roughly 250 KB; do not read it whole. Start with the
records the keyword channel flagged, then sweep the rest by title:

```bash
# Keyword-channel hits first — highest prior, usually a handful
python3 -c "
import json; d=json.load(open('/tmp/papers.json'))
for p in d['papers']:
    if p['extra'].get('keyword_match'): print('★', p['source'], p['title'])
"
# Then everything else, titles only
python3 -c "
import json; d=json.load(open('/tmp/papers.json'))
for i,p in enumerate(d['papers']):
    if not p['extra'].get('keyword_match'): print(i, p['source'], p['title'])
"
```

The flag is a prior, not a filter. Subject-area records without it still
matter — the papers a keyword alert ranks lowest and a human ranks highest
live there, which is the whole reason ranking is your job and not a `grep`.

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

`also_in` means the record was seen more than once — **check what kind of
duplicate before describing it**, because most are not what they look like:

- **`merge_reason: exact-doi` between two preprint sources** (typically
  `biorxiv` + `europepmc`) is the *same preprint* arriving through two
  channels. There is no journal version. Saying there is invents one — this is
  the common case, usually the large majority of merges.
- **A merge involving `pubmed`** is a genuine preprint-and-journal pair. Cite
  the journal version and mention the preprint, which is often the one they can
  actually read without a subscription.

When in doubt, look at `also_in[].source`: if no member is `pubmed`, do not
claim the paper has been published.

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

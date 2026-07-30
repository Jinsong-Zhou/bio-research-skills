---
name: literature-tracking
description: Track new life-science papers across arXiv q-bio, bioRxiv, medRxiv and PubMed in one pass, merging preprints with their published journal versions so nothing appears twice, then rank what is left against the user's research interests. Use when asked what is new in a field, for a literature digest or weekly roundup, to catch up after time away, to monitor a topic or set of authors, or whenever a query spans more than one preprint server or literature database. Triggers on "anything new on X", "papers this week", "literature digest", "what did I miss", "keep me updated on", "recent preprints", or any mention of arXiv q-bio, bioRxiv, medRxiv or PubMed together.
license: MIT
allowed-tools: Bash Read
compatibility: Needs network access and Python 3.9+. Scripts use only the standard library — no packages to install, no credentials required. Set BIO_RESEARCH_CONTACT to your email so Crossref and NCBI can identify you. NCBI_API_KEY is optional and buys headroom against a ban rather than speed: it raises NCBI's server-side limit to 10 requests/second, but the client paces to 3/s either way.
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

Default to the last 7 days across arXiv, bioRxiv, PubMed and Europe PMC — that
is exactly what `--sources` defaults to. Widen to `30d` if the topic is
slow-moving or the last check was long ago.

**medRxiv is off by default.** Clinical questions need
`--sources arxiv biorxiv medrxiv pubmed europepmc` *as well as*
`--medrxiv-categories`; passing the categories alone is a usage error and the
script stops rather than quietly searching nothing.

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
same preprints and *does* search full text. Coverage measured over five days
ran 43–78% (66% overall), and indexing lags about a day — **the newest day is
the worst covered**, which is the day a tracking query cares about most. So it
supplements the direct fetch rather than replacing it — but
records carry the same DOI, so the two views merge, and anything reached
through the keyword channel comes out flagged `extra.keyword_match`. Use that
flag to decide what to read first; it never removes anything from the list.

Pick bioRxiv areas from the profile, not from habit. `biochemistry`,
`biophysics` and `molecular biology` suit structural work; add
`synthetic biology` and `bioengineering` whenever protein *design* is in scope —
leaving them out is how a design-focused digest ends up with no design papers.
`bioinformatics` skews towards tool and pipeline announcements, so it tends to
be high-volume and low-yield for a structural lab; include it if methods
development is the point.

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

Useful flags: `--sources` to change which databases are queried (this is the
one that gates everything else), `--until` for a closed window, `--pubmed-term`
for a raw PubMed query with field tags, `--medrxiv-categories` for clinical work
(with `medrxiv` added to `--sources`), `--crossref on|off` to override the
Crossref dedup rule. `--help` lists everything.

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
    "pubmed":    {"status": "ok", "fetched": 200, "available": 556,
                  "coverage": "truncated", "truncated": true,
                  "covers": ["2026-07-28", "2026-07-29"],
                  "covers_field": "entrez_date", "notes": []},
    "europepmc": {"status": "skipped", "reason": "no --keywords given, …",
                  "fetched": 0, "coverage": "unknown"}
  },
  "truncated_sources": ["pubmed"],
  "unknown_coverage_sources": [],
  "skipped_sources": {"europepmc": "no --keywords given, …"},
  "crossref": {"requested": "auto", "enabled": false,
               "reason": "7-day window is under 60 days, …", "lookups": 0},
  "duplicates_merged": 20,
  "merges_by_tier": {"exact-doi": 19, "title-fingerprint": 1},
  "rule_matches": {"exact-doi": 19, "title-fingerprint": 18}
}
```

Every requested source has a row with a `status` of `ok`, `failed` or
`skipped`. A source that ran but returned nothing is not the same as one that
never ran, and the difference decides whether "nothing new this week" is true.

- **`errors` non-empty** — say which source failed and that the digest is
  partial. Never present an incomplete sweep as a complete one. An entry with
  a `kind` of `unexpected` is a bug in the adapter, not an outage; report it as
  one.
- **`skipped_sources` non-empty** — a source was requested but not queried, and
  `reason` says why. The usual case is `europepmc` without `--keywords`: the
  keyword channel is the only relevance signal in the report, so `keyword_matched:
  0` here means *nothing was searched*, not *nothing matched*. Fix the flags and
  re-run before concluding the week was quiet.
- **`truncated_sources` non-empty** — ⚠️ **this is not a random sample.** Every
  one of these APIs returns its newest records first, so a truncated fetch
  drops the *early days of the window entirely*. Check `covers` — and check
  `covers_field` with it, because PubMed's span is measured on Entrez date, not
  publication date. Re-run with a higher `--max-per-source` before writing
  anything, or the digest silently describes the wrong week — and cross-source
  dedup misses pairs whose other half fell in the discarded days.
- **`unknown_coverage_sources` non-empty** — the source ran but never said how
  much existed, so `truncated: false` there is an absence of evidence rather
  than a complete sweep. Treat it like truncation when it matters.
- **`notes` non-empty** — records the source fetched but could not parse. These
  are *not* truncation; raising `--max-per-source` will not bring them back.
- **`crossref.enabled: false`** — rule 4 did not run, and `reason` says why.
  With the default 7-day window `auto` always turns it off, so this is the
  normal state; `lookups: 0` on its own would be indistinguishable from "ran it
  and found nothing to look up". If `crossref_skipped` is above zero, that only
  happens with `--crossref on`, and raising `--max-crossref-lookups` buys
  minutes of requests for merges that a short window cannot produce. Only raise
  it on a 60+ day sweep.
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
duplicate before describing it**, because most are not what they look like.

The test is the **top-level `source`**, not `also_in`. The merged record keeps
the highest-ranked source (`pubmed` > `biorxiv`/`medrxiv` > `arxiv` >
`europepmc`), so a genuine preprint-and-journal pair comes out as
`"source": "pubmed"` with the preprint listed inside `also_in`. PubMed will
therefore never *appear* in `also_in` — looking for it there marks every real
publication as unpublished.

| What you see | What it is |
|---|---|
| `source: pubmed`, `also_in` has a preprint server | A genuine preprint-and-journal pair. Cite the journal version, mention the preprint — often the copy they can read without a subscription. |
| `source` and every `also_in` entry are preprint sources | The *same preprint* through two channels, typically `biorxiv` + `europepmc`. **There is no journal version.** This is the common case — usually the large majority of merges. |

So: **if `source` is not `pubmed`, do not claim the paper has been published.**

`merge_reason` is a `+`-joined *set*, not a single value — the usual
`biorxiv` + `europepmc` pair matches on both DOI and title, giving
`"exact-doi+title-fingerprint"`. Test for membership, never equality:

```python
"exact-doi" in paper["merge_reason"].split("+")
```

`also_in[].title` carries the other record's title. If it disagrees with the
primary's, the merge is wrong — say so rather than presenting one paper.

Close with what was searched: window, sources, how many were scanned, how many
made the cut. Trust comes from showing the funnel.

## What the script guarantees, and what it does not

**Guarantees.** Structured arXiv queries that actually filter. Validated
bioRxiv subject areas. Day-precision PubMed dates. Real pagination, from the
newest end. Preprint and journal versions merged by four rules, with the ones
that agreed recorded in `merge_reason`. A row in `coverage_by_source` for every
requested source, so a source cannot go missing without saying so.

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

---
name: paper-deep-reading
description: Deep-read one paper end to end — explain what it does, then judge whether its conclusions actually hold and whether it is worth acting on — and deliver the result as a Word document. Use when handed a PDF, an arXiv id, a DOI or a preprint link and asked to read it properly, go through it carefully, evaluate it, critique it, or write it up. Triggers on "read this paper", "deep read", "精读", "help me understand this paper", "is this paper any good", "does this hold up", "review this preprint", "write up notes on this paper", or any request pairing one specific paper with more than a summary.
license: MIT
allowed-tools: Bash Read Write
compatibility: Needs network access and Python 3.9+. The bundled scripts use only the standard library — no packages to install, no credentials required. Set BIO_RESEARCH_CONTACT to your email so bioRxiv and Europe PMC can identify you. Word and slide output are rendered by the `docx` and `pptx` skills from `anthropics/skills`, which are licensed separately and need Node.js; without them the note still renders to Markdown.
metadata:
  version: "0.1"
  skill-author: "Jinsong Zhou"
---

# Paper deep reading

One paper, read properly: what it does, whether its conclusions hold, and
whether the user should act on it. The second half is the point — anything can
summarise a paper, and a summary is not what someone asks for when they say
"read this properly".

`scripts/fetch.py` gets the PDF or says why it could not.
`scripts/note.py` holds you to a shape — every claim you credit to the paper
has to name where in the paper it lives.
**You** do the reading, the judging and the writing. That part is not
scriptable and no attempt is made to script it.

## Workflow

**Run the scripts from the directory holding this `SKILL.md`** — they import
each other by bare name, so `scripts/` has to be the working root.

### 1. Get the paper

```bash
cd /path/to/skills/paper-deep-reading
python3 scripts/fetch.py <reference> --out-dir /tmp/papers > /tmp/fetch.json
```

`<reference>` can be a local `.pdf` path, an arXiv id or URL, a DOI, a
bioRxiv/medRxiv link, a PMCID or a PMID. Three routes are covered — arXiv,
bioRxiv/medRxiv, and Europe PMC's open-access mirror of PubMed Central. A
paywalled journal article is not one of them, and the script will say so
instead of returning something that looks like a paper.

Read `fetch.json` before anything else:

- **`fulltext: "abstract-only"`** — ⚠️ stop and tell the user. You can still
  write something, but it is a summary of an abstract, and it must be labelled
  that way. An assessment built on an abstract is worthless: abstracts contain
  claims and no evidence, which is precisely the half you are supposed to be
  auditing. Offer the alternative — if they can get the PDF themselves, pass
  the local path and start over.
- **`warnings`** — the useful one is *"this preprint was later published as
  …"*. The version you are about to read predates peer review. Say which
  version the note describes, and check whether the published one differs.
- **`path`** — read it with the Read tool. It is a real PDF; the script
  verified the bytes rather than the status code.

### 2. Find out who you are reading for

The note ends with a section on relevance to the user's own work, and that
section is the only one that needs information the paper does not contain.
Get it in this order, and stop at the first that works:

1. What they have said in this conversation about what they work on
2. Project memory or `CLAUDE.md`
3. Ask them — one sentence, once
4. Nothing available → set `relevance.status` to `"no-background-provided"`

Step 4 is a real answer. The renderer prints a line saying the section was left
empty on purpose. Inventing a plausible-sounding connection is worse than an
empty section, because the user cannot tell the two apart.

### 3. Read in this order — not front to back

The order matters more than it sounds. Reading a paper the way it is printed
means meeting the authors' interpretation of their data before the data
itself, and you do not fully recover from that.

1. **Abstract, then the last paragraph of the Introduction.** That paragraph is
   where papers list their contributions. Copy the claims out **verbatim** —
   these are what you audit in step 5, and the exact wording is the thing being
   audited. "Improves binding affinity prediction" and "improves binding
   affinity prediction by 12% over AlphaFold3" are not the same claim and do
   not need the same evidence.
2. **The figures and tables, before the prose around them.** Look at the
   numbers first. What is the spread? Is there an error bar? How many
   conditions? Which comparison is conspicuously absent?
3. **Methods, holding one question**: how was each number in step 2 produced?
   Do not read Methods for completeness. Read it to trace reported numbers back
   to procedures.
4. **Results prose.** Now read what the authors say their data means, and mark
   every sentence that is broader than the figure it cites.
5. **Discussion and Limitations.** What do they actually concede? A limitations
   section that only lists future work concedes nothing.
6. **Supplementary and appendices.** The ablation that did not work, the
   hyperparameter sensitivity, the failure cases and the full baseline table
   live here. Skipping it is the single most common way a paper's weakest point
   goes unmentioned.
7. **Related work, last.** Once you know what the paper did, you can judge
   whether it is compared against the right things. Before that, you cannot.

### 4. Write the first half — what the paper does

Four fields, in the user's language, technical terms and figure references left
in the paper's:

- **problem** — what they are trying to solve and why it is hard. Where were
  the previous attempts stuck? A problem statement that does not say what was
  blocking people is a topic, not a problem.
- **method** — how it works, and specifically which design choice is doing the
  work. Most papers have one. Name it.
- **experiments** — the setup: datasets, baselines, metrics, how much data,
  how many runs. This is the section you will lean on in step 5, so be
  concrete.
- **findings** — what came out, with pointers (`Fig. 3b`, `Table 2`). Report
  the results here; save what you think of them for the next section.

Write this half so someone outside the subfield can follow it. Explain the
terms that need explaining. This is the "先讲清楚" half.

### 5. Audit the claims — this is what a deep read is for

Take the verbatim claim list from step 3.1. Every claim gets a row:

| field | what goes in it |
|---|---|
| `claim` | the authors' claim, in their scope, not softened |
| `evidence` | **where in the paper it is supported** — `Table 2`, `Fig. 3b`, `Sec. 4.1`, `Supplementary Fig. 7` |
| `confidence` | `high` / `medium` / `low` — how well that evidence supports *this* claim |
| `issue` | what is wrong or missing, if anything |

Three rules:

- **Evidence is a pointer into the paper.** "The authors state that…" is the
  claim restated, not evidence for it. `note.py validate` rejects an evidence
  field that names no figure, table, section or page.
- **A claim with no evidence anywhere is a finding, not a blank.** Set
  `evidence` to null and say so in `issue` — "asserted in the abstract and the
  discussion; no experiment in the paper measures it". That row is often the
  most valuable one in the table.
- **Confidence is about the evidence, not about the paper.** A paper you like
  can have a low-confidence claim; a paper you find unconvincing can have
  high-confidence ones. Grade each row on its own.

Read `references/credibility-checks.md` before writing this section. It is the
checklist of what to look for — baseline vintage, missing ablations, variance
reporting — including the failure modes specific to computational biology, of
which **train/test homology leakage** is by far the most common and the least
often disclosed.

Then fill `limitations`: what the authors acknowledged, and separately what
they did not. Keeping those apart is deliberate. "The authors note that…" and
"the paper never addresses…" are very different statements about a group.

### 6. Reach a verdict

`decision` is one of `follow-up`, `watch`, `skip`, and `reasoning` has to say
why in terms of the audit above, not in terms of how interesting the paper is.
Add `cost` (what acting on it would take — is the code out, are the weights
available, roughly how much compute) and `next_steps` when the decision is to
follow up.

A verdict of `skip` on a well-executed paper is a legitimate outcome; so is
`follow-up` on a flawed one whose core idea is worth having. Say which you mean.

### 7. Validate and render

Write the note as JSON. `python3 scripts/note.py template` prints the shape;
`references/note-schema.md` explains each field.

```bash
python3 scripts/note.py validate /tmp/note.json
python3 scripts/note.py render /tmp/note.json -o /tmp/note.md
```

Fix what validation reports rather than passing `--force`. Every check it makes
corresponds to a way a note reads as more grounded than it is.

### 8. Deliver as Word

The default deliverable is a `.docx`. Use the **`docx` skill** from
`anthropics/skills`, giving it the Markdown from step 7:

```
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills
```

That skill is licensed separately from this one — © Anthropic, and its terms
run through the user's agreement with Anthropic — and it needs Node.js plus the
`docx` npm package. It is not vendored here and cannot be.

**If it is unavailable, deliver `/tmp/note.md` and say why.** A Markdown note in
hand beats a Word document that never arrived. Do not fall back to writing raw
`.docx` XML yourself; it is a poor use of the user's time and yields a worse
document than the Markdown.

Layout that survives the conversion: the claim table as a real table, the two
parts as `Heading 1`, each field as `Heading 2`. Keep the abstract-only banner
if there is one — it is the most important sentence in the document.

### 9. Offer slides, do not assume them

If the note is going to a group meeting, the `pptx` skill from the same plugin
renders it. **Ask first.** Slides are a different document with different
content — the claim table rarely survives contact with a slide, and the verdict
usually becomes the first slide rather than the last.

## What the scripts guarantee, and what they do not

**Guarantee.** A file that is actually a PDF, or an explicit statement that
there is none. The right preprint server and version for a `10.1101/` DOI. A
note whose claims name their evidence and whose empty sections are labelled as
empty.

**Do not.** Read, judge, summarise or write. Check that a figure reference is
*correct* — validation checks that you cited something, not that you cited the
right thing. Parse the PDF; you read it directly.

## Traps

Before modifying `scripts/fetch.py`, read `references/fulltext-sources.md`. It
records what each API actually did when measured, including the two that cost a
bug: bioRxiv writes the literal string `"NA"` for an unpublished preprint, and
preprint DOIs now carry either of two prefixes with neither mapping to a single
server.

- **The abstract is an argument, not a description.** Read it for the claim
  list, then set it aside. If your assessment agrees with the abstract on every
  point, you have summarised rather than audited.
- **Preprints drift.** `fetch.py` reports when a preprint was later published.
  The reviewed version can differ substantially, and a note that does not say
  which version it read cannot be checked later.
- **Do not launder uncertainty into fluency.** If the method section is unclear
  about something load-bearing, that goes in `issue` as a finding about the
  paper. Writing a confident paraphrase of something you could not follow is
  the one failure mode of this skill that is invisible in the output.
- **`fetch.py` covers three routes, not all of them.** Paywalled journal
  articles without an open-access version cannot be retrieved, by design.
  Say so and ask for the PDF.

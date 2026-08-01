---
name: paper-deep-reading
description: >-
  This skill should be used when deep-reading one paper end to end: explaining
  what it does, judging whether its conclusions actually hold and whether it is
  worth acting on, and delivering the result as a Word document. Applies to a
  PDF, an arXiv id, a DOI or a preprint link. Triggers on "read this paper",
  "deep read", "精读", "help me understand this paper", "is this paper any good",
  "does this hold up", "review this preprint", "write up notes on this paper",
  or any request pairing one specific paper with more than a summary.
license: MIT
allowed-tools: Bash, Read, Write
compatibility: >-
  Needs network access and Python 3.9+. The bundled scripts use only the standard
  library — no packages to install, no credentials required. Set
  BIO_RESEARCH_CONTACT to your email so bioRxiv and Europe PMC can identify you.
  Word and slide output are rendered by the `docx` and `pptx` skills from
  `anthropics/skills`, which are licensed separately and need Node.js; without
  them the note still renders to Markdown.
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
`scripts/note.py` enforces a shape — every claim credited to the paper has to
name where in the paper it lives.
The reading, the judging and the writing are **not** scriptable, and no attempt
is made to script them.

## Workflow

**Invoke the scripts by path** — `python3 scripts/fetch.py`, from any working
directory. They import each other by bare name, and Python puts the script's own
directory on `sys.path`, so `scripts/` becomes the import root on its own.

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

- **`fulltext: "abstract-only"`** — ⚠️ stop and tell the user. Something can
  still be written, but it is a summary of an abstract and must be labelled
  that way. An assessment built on an abstract is worthless: abstracts contain
  claims and no evidence, which is precisely the half being audited. Offer the
  alternative — if they can get the PDF themselves, pass the local path and
  start over.
- **`warnings`** — the useful one is *"this preprint was later published as
  …"*. That version predates peer review. Say which version the note
  describes, and check whether the published one differs.
- **`path`** — read it with the Read tool. It is a real PDF; the script
  verified the bytes rather than the status code.

### 2. Find out who the note is for

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
itself, and that first impression does not fully wash out.

1. **Abstract, then the last paragraph of the Introduction.** That paragraph is
   where papers list their contributions. Copy the claims out **verbatim** —
   these are what step 5 audits, and the exact wording is the thing being
   audited. "Improves binding affinity prediction" and "improves binding
   affinity prediction by 12% over AlphaFold3" are not the same claim and do
   not need the same evidence.

   **Decide `paper.type` here too** — `computational`, `experimental`,
   `method`, `resource` or `theory`. It changes what to look for in the rest
   of the pass and how step 4's pipeline section decomposes. When a paper both
   builds a model and validates it at the bench, ask which one the paper would
   still be worth publishing without.
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
7. **Related work, last.** Only once the paper's own contribution is clear can
   its choice of comparisons be judged. Before that, it cannot.

### 4. Write the first half — teach the paper

**Write as a professor explaining to a capable colleague from a neighbouring
field, not as an abstract-shortening service.** The test is whether a reader
who has never seen this paper could, afterwards, say why the work was hard and
why this particular idea was a reasonable bet. A section that only restates
what the paper did has failed even if every sentence is true.

Five fields, in the user's language, with technical terms and figure references
left in the paper's. Each answers a question the previous one raises:

| Field | Carries |
|---|---|
| `problem` | what the work is after, and **why that is hard** — the obstacle, not the topic |
| `approach` | the idea, and why it should work — answering each obstacle in `problem` one by one |
| `pipeline` | what it actually does, step by step; decomposition depends on `paper.type` |
| `mechanism` | **why the idea has the effect it has.** The hardest field and the most valuable |
| `findings` | what came out, with pointers (`Fig. 3b`). Report here; judge in step 5 |

Read [references/writing-the-explanation.md](references/writing-the-explanation.md)
before writing these. It carries what each field has to contain, the five
`paper.type` decompositions for `pipeline`, the three things that belong in
`pipeline` regardless of type — model system, measured-versus-concluded, sample
size — and the register to write in.

`scripts/note.py validate` warns when one of these fields is too short to be
doing this job. It is a floor, not a target: clearing it is not the same as
having explained anything.

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
  claim restated, not evidence for it. Given the full text, `note.py validate`
  rejects an evidence field that names no figure, table, section or page.
  Supplementary citations count and are expected — `Fig. S3`, `Table S1`. On an
  `abstract-only` note the demand is dropped, because there is nothing to point
  at.
- **A claim with no evidence anywhere is a finding, not a blank.** Set
  `evidence` to null and say so in `issue` — "asserted in the abstract and the
  discussion; no experiment in the paper measures it". That row is often the
  most valuable one in the table.
- **Confidence is about the evidence, not about the paper.** An appealing paper
  can carry a low-confidence claim; an unconvincing one can carry
  high-confidence claims. Grade each row on its own.

Read `references/credibility-checks.md` before writing this section. Alongside
the general checks — baseline vintage, missing ablations, variance reporting —
it carries the three that have no equivalent outside biology and that a
reviewer from another field would read straight past:

- **Correlation presented as mechanism.** Knockdown alone is one experiment
  short of causal; rescue, dose dependence and the abolishing point mutation
  are what close it.
- **The model system and the distance to the claim.** HEK293 is not a neuron,
  overexpression is itself a perturbation, and *in vitro* is not a cell.
- **The proxy and the target.** mRNA is not protein, expression is not
  function, colocalisation is not interaction, and a predicted structure is
  not a structure.

For computational work, **train/test homology leakage** is the most common and
least often disclosed failure of all — check it first.

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
`follow-up` on a flawed one whose core idea is worth having. Say which is meant.

### 7. Validate and render

Write the note as JSON. `python3 scripts/note.py template` prints the shape;
`references/note-schema.md` explains each field.

**Plain text, no Markdown.** The note carries text; emphasis is the renderer's
job. `**bold**`, backticks, `#` headings, `-` bullets, links and HTML tags are
all rejected — Word renders them as literal characters. A blank line starts a
new paragraph, and list items go in the fields that are already lists
(`next_steps`, `limitations.*`, `authors`). The check reads the whole note, so
it covers `paper.venue` and `paper.url` too. Statistical significance is not
markup and is not rejected: write `(***, p < 0.001)` as a paper would.

**Write lists as lists.** `"next_steps": "watch for a code release"` is a
string, not a list of one, and it used to render as a numbered list with one
character per item. Validation now rejects it; the fix is `[...]`.

```bash
python3 scripts/note.py validate /tmp/note.json
python3 scripts/note.py render /tmp/note.json -o /tmp/note.md
```

Fix what validation reports rather than passing `--force`. Every check it makes
corresponds to a way a note reads as more grounded than it is. `--force` still
prints each error it overrode, so the output is never quietly incomplete.

### 8. Deliver as Word

The default deliverable is a `.docx`. Render the note to **blocks** and hand
those to the **`docx` skill** from `anthropics/skills`:

```bash
python3 scripts/note.py render /tmp/note.json --format blocks -o /tmp/blocks.json
```

```
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills
```

Blocks, not the Markdown. Parsing a table back out of pipe characters is lossy,
and the headings are already translated into the note's language — a renderer
only has to know eleven shapes: `title` `banner` `h1` `h2` `p` `note` `label`
`fields` `bullets` `numbered` `table`. `references/note-schema.md` lists what
each carries.

That skill is licensed separately from this one — © Anthropic, and its terms
run through the user's agreement with Anthropic. It is not vendored here and
cannot be. (The `docx` npm package it drives is MIT and a different thing.)

**If it is unavailable, deliver `/tmp/note.md` and say why.** A Markdown note in
hand beats a Word document that never arrived.

Four layout facts, measured by building the document and looking at it:

- **Table column widths must sum to the content width.** A4 with the default
  1440 DXA margins leaves **9026**. Set `columnWidths` on the table *and*
  `width` on every cell, both in DXA — percentages break in Google Docs.
- **CJK needs an `eastAsia` font.** `font: { name: "Calibri", eastAsia: "PingFang SC" }`
  on the default run style renders Chinese notes correctly; without it the
  glyph fallback is the renderer's guess.
- **Put an empty paragraph after a table**, or an adjacent table merges into it.
- **The `banner` block is the most important thing on the page** when it is
  present. Give it shading and a border, not italics — it is the sentence that
  tells the reader this note was written without the paper.

### 9. Offer slides, do not assume them

If the note is going to a group meeting, the `pptx` skill from the same plugin
renders it — from the same blocks. **Ask first.** Slides are a different document with different
content — the claim table rarely survives contact with a slide, and the verdict
usually becomes the first slide rather than the last.

## What the scripts guarantee, and what they do not

**Guarantee.** A *downloaded* file that is actually a PDF, or an explicit
statement that there is none. The right preprint server and version for a
preprint DOI, whichever prefix it carries. A note whose claims name their
evidence and whose empty sections are labelled as empty.

**Do not.** Read, judge, summarise or write. Check that a figure reference is
*correct* — validation checks that something was cited, not that the right
thing was. Parse the PDF; it is read directly. Vouch for a local file: a path
passed in is reported as full text with a warning if it does not start with
`%PDF-`, because there may have been a reason to point at it.

## Traps

Before modifying `scripts/fetch.py`, read `references/fulltext-sources.md`. It
records what each API actually did when measured, including the two that cost a
bug: bioRxiv writes the literal string `"NA"` for an unpublished preprint, and
preprint DOIs now carry either of two prefixes with neither mapping to a single
server.

- **The abstract is an argument, not a description.** Read it for the claim
  list, then set it aside. An assessment that agrees with the abstract on every
  point has summarised rather than audited.
- **Preprints drift.** `fetch.py` reports when a preprint was later published.
  The reviewed version can differ substantially, and a note that does not say
  which version it read cannot be checked later.
- **Do not launder uncertainty into fluency.** If the method section is unclear
  about something load-bearing, that goes in `issue` as a finding about the
  paper. A confident paraphrase of something that could not be followed is the
  one failure mode of this skill that is invisible in the output.
- **`fetch.py` covers three routes, not all of them.** Paywalled journal
  articles without an open-access version cannot be retrieved, by design.
  Say so and ask for the PDF.

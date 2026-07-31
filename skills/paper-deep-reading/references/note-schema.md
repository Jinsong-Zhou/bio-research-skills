# Note schema

The JSON the agent writes and `scripts/note.py` renders. `python3
scripts/note.py template` prints an empty one.

The schema exists to make two things impossible: crediting the paper with a
claim without saying where the claim lives, and leaving a section blank in a
way the reader cannot detect.

---

## Top level

| field | type | notes |
|---|---|---|
| `language` | `"en"` \| `"zh"` | Which language the note is written in. Controls the rendered headings only — the content is yours. Defaults to `en` if absent. |
| `paper` | object | Bibliographic identity |
| `understanding` | object | Part 1 — what the paper does |
| `assessment` | object | Part 2 — whether it holds up |
| `relevance` | object | Connection to the reader's own work |

## `paper`

| field | required | notes |
|---|---|---|
| `title` | ✅ | |
| `authors` | | List of strings. The renderer shows the first three plus "et al." |
| `year` | | |
| `venue` | | Journal, conference, or the preprint server |
| `doi` | | |
| `url` | | |
| `type` | ✅ | `computational` \| `experimental` \| `method` \| `resource` \| `theory`. **This decides how `pipeline` decomposes** — a model has training and inference, a cryo-EM structure has sample prep and reconstruction. Choosing wrong produces a section describing a paper nobody wrote. A paper that both builds a model and validates it in the lab is `computational` if the model is the contribution, `experimental` if the validation is. |
| `fulltext` | ✅ | `"full"` or `"abstract-only"`. Copy from `fetch.py`'s report — do not set `"full"` because it feels better. `"abstract-only"` puts a banner at the top of the document, which is the honest thing for a note written without the paper. |

## `understanding`

All five required, all free text in the note's language. They are ordered so
each answers a question the previous one raises; SKILL.md step 4 has the full
instructions and the per-`paper.type` decomposition for `pipeline`.

| field | what belongs in it |
|---|---|
| `problem` | What they are solving, **why it is hard**, and where previous attempts got stuck *and why*. A problem statement that names no obstacle is a topic. |
| `approach` | The central idea, and how it answers the obstacles in `problem` **one by one**. An obstacle with no answer here should be named as unanswered. |
| `pipeline` | What it concretely does, in sequence. Decomposes differently per `paper.type`. Must state the model system, what was physically measured versus what is concluded, and the replication. |
| `mechanism` | Why the idea has the effect it has. Name the one thing carrying the result, whether the paper isolates it, and where it would stop working. For a biological claim this is where correlation-versus-causation lives. |
| `findings` | What came out, with pointers (`Fig. 3b`, `Table 2`). Results only; judgement goes in `assessment`. |

Two soft checks, both warnings rather than errors:

- **Depth.** `problem`, `approach`, `pipeline` and `mechanism` warn below
  `MIN_DEPTH` weighted characters (CJK counts double, so one threshold serves
  both languages). It catches "the method improves accuracy" and nothing
  subtler — clearing it is a floor, not evidence of having explained anything.
- **Pointers.** `findings` with no figure/table/section reference warns; some
  papers genuinely report one headline number in prose.

## `assessment`

### `claims` — a list, at least one entry

| field | required | notes |
|---|---|---|
| `claim` | ✅ | The authors' claim at the authors' scope. Do not soften it; the scope is part of what is being audited. |
| `evidence` | | Where in the paper it is supported: `"Table 2"`, `"Fig. 3b"`, `"Sec. 4.1"`, `"Supplementary Fig. 7"`. Must name a figure, table, section, equation, page or appendix when `paper.fulltext` is `"full"`. |
| `confidence` | ✅ | `"high"` \| `"medium"` \| `"low"` — how well *that evidence* supports *that claim*. Not how much you like the paper. |
| `issue` | | What is wrong or missing. Required when `evidence` is empty. |

**`evidence: null` is a legitimate and often important row.** A claim the paper
asserts but never measures is a finding. Set `evidence` to null and explain in
`issue`: `"asserted in the abstract and discussion; no experiment measures it"`.
Leaving both empty is the one thing validation rejects outright.

### `limitations`

| field | type | notes |
|---|---|---|
| `acknowledged` | list of strings | What the authors concede themselves |
| `unstated` | list of strings | What you found that they do not address |

Both are required and either may be empty. They are separate fields on purpose:
"the authors note the method is untested on membrane proteins" and "the paper
never addresses membrane proteins" say very different things about a group, and
merging them into one list loses that.

### `verdict`

| field | required | notes |
|---|---|---|
| `decision` | ✅ | `"follow-up"` \| `"watch"` \| `"skip"` |
| `reasoning` | ✅ | Why, in terms of the claim audit. "Interesting" is not a reason. |
| `cost` | | What acting on it would take: code availability, weights and their licence, rough compute. See the reproducibility section of `credibility-checks.md`. |
| `next_steps` | | Ordered list, when the decision is `follow-up` |

## `relevance`

| field | required | notes |
|---|---|---|
| `status` | ✅ | `"written"` or `"no-background-provided"` |
| `text` | when `written` | The connection to their work |

`"no-background-provided"` renders as an explicit line saying the section was
left empty deliberately. That is the correct output when the reader's research
background is unknown — see SKILL.md step 2.

---

## The blocks form

`note.py render --format blocks` emits the same note as a flat list of typed
blocks. This is what a renderer that is not written in Python consumes — the
`docx` and `pptx` skills, for instance.

It exists because the two obvious alternatives are both worse. Handing a
renderer the Markdown means parsing a table back out of pipe characters.
Handing it the raw note means reimplementing the heading translations in
whatever language that renderer is written in. Blocks carry the labels already
resolved, so eleven shapes cover the whole document:

| type | fields | renders as |
|---|---|---|
| `title` | `text` | document title |
| `banner` | `lead`, `text` | shaded, bordered callout — only when `fulltext` is `abstract-only` |
| `h1` | `text` | section heading |
| `h2` | `text` | subsection heading |
| `p` | `text`, optional `lead` | paragraph; `lead` is a bold run before an em dash |
| `note` | `text` | italic aside — the deliberately-empty relevance section |
| `label` | `text` | bold standalone line introducing a list |
| `fields` | `items[{label, value}]` | bulleted label/value pairs |
| `bullets` | `items[]` | bulleted list |
| `numbered` | `items[]` | ordered list |
| `table` | `header[]`, `rows[][]` | the claim table |

**Blocks carry no markup.** No `**bold**`, no `> quote`, no `_italic_` — the
emphasis is in the block type, and each renderer applies its own. A block whose
text contains Markdown syntax is a bug in `build_blocks`, not something for the
renderer to strip.

Empty lists still render: `bullets` with no items becomes `["—"]`, so an empty
limitations section is visibly empty rather than absent.

---

## Worked fragment

```json
{
  "language": "zh",
  "paper": {
    "title": "A structure-aware model for protein-ligand affinity",
    "authors": ["A. Author", "B. Author"],
    "year": 2026,
    "venue": "bioRxiv",
    "doi": "10.1101/2026.01.15.575681",
    "type": "computational",
    "fulltext": "full"
  },
  "assessment": {
    "claims": [
      {
        "claim": "在 PDBbind core set 上比 AF3 提升 12% Pearson r",
        "evidence": "Table 2",
        "confidence": "medium",
        "issue": "单一数据集，单次运行，未报方差；AF3 baseline 用的是作者自己复现的版本（Sec. 4.2）"
      },
      {
        "claim": "方法可推广到未见过的蛋白家族",
        "evidence": null,
        "confidence": "low",
        "issue": "摘要和讨论都这么说，但没有任何实验按家族划分测试集；Sec. 3.1 只提到 30% 序列一致性去冗余，不排除同源泄漏"
      }
    ],
    "limitations": {
      "acknowledged": ["作者承认未在膜蛋白上测试（Sec. 6）"],
      "unstated": ["全文未讨论 MSA 层面的同源泄漏", "没有物理合理性检查"]
    },
    "verdict": {
      "decision": "watch",
      "reasoning": "核心想法有价值，但 12% 这个数字建立在自己复现的 baseline 上，泛化声明无实验支撑。等作者放出代码、或有第三方在盲测集上复现再跟进。",
      "cost": "代码未发布，权重未发布；复现需自行实现",
      "next_steps": ["关注是否发布代码", "若发布，先用官方 AF3 权重重跑 baseline"]
    }
  },
  "relevance": {"status": "no-background-provided", "text": ""}
}
```

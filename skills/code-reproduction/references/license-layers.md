# The four licence layers

A repository's `LICENSE` file is not its licence. In machine-learning research
a single checkout routinely carries four different grants, and the permissive
one is almost always the one at the top:

| Layer | Covers | Typical terms | What it decides |
|---|---|---|---|
| **code** | the source you clone | Apache-2.0, MIT, BSD | whether you may fork and publish changes |
| **weights** | the checkpoints you download | NVIDIA Open Model, Llama Community, OpenRAIL, bespoke | **whether you may use the output** |
| **data** | training indices, splits, benchmark sets | CC-BY, CC-BY-NC, DUA | whether you may redistribute what you trained on |
| **third-party** | vendored dependencies | anything, stacked | what travels with a derivative you ship |

The weights layer is the one that matters and the one nobody reads. It does
not stop the code running, so it never surfaces during a reproduction — it
surfaces afterwards, when someone asks whether the result can go into a
product.

## Three repositories from one lab

Verified 2026-07-31 against the live repositories. Same authors, same model
family, three consecutive papers, three different answers:

| | [`proteina`](https://github.com/NVIDIA-BioNeMo/proteina) (ICLR 2025) | [`la-proteina`](https://github.com/NVIDIA-BioNeMo/la-proteina) (2025) | [`Proteina-Complexa`](https://github.com/NVIDIA-BioNeMo/Proteina-Complexa) (ICLR 2026) |
|---|---|---|---|
| Layout | one `LICENSE` file | `LICENSE/` **directory**, 4 files | `LICENSE` pointer + `licenses/`, 4 files |
| Code | NVIDIA License | Apache-2.0 | Apache-2.0 |
| Weights | *same file* | NVIDIA **Open Model** License | NVIDIA **Open Model** License |
| Data | *same file* | CC-BY-4.0 | CC-BY-4.0 |
| Commercial use | **No** — "non-commercially … research or evaluation purposes only" (`LICENSE` §3.3) | Yes | Yes |
| GitHub's detector says | `NOASSERTION / Other` | **nothing at all** | `NOASSERTION / Other` |

Three things to take from that table.

**The terms reverse between the first two.** `proteina` is uniformly
non-commercial: its README states that "source code, model weights, dataset
indices and auxiliary files are released under an NVIDIA license for
non-commercial or research purposes only". Its successor `la-proteina`, same
lab, splits into three layers and puts the weights under a licence that grants
commercial use. Nothing in either repository name tells you this. Reading one
and assuming the other is the single most likely way to get this wrong.

**Two NVIDIA licences differ by one word.** The **NVIDIA License**
(`proteina`) restricts use to non-commercial purposes, with a carve-out
letting NVIDIA itself use it commercially. The **NVIDIA Open Model License
Agreement** (`la-proteina`, `Proteina-Complexa`) opens with "Models are
commercially usable". They are not variants of each other, and a check that
greps for "NVIDIA" and stops has learned nothing.

**GitHub's own detector is no help on any of them.** Two report
`NOASSERTION`; for `la-proteina` the API returns 404 for the licence endpoint
entirely, because `LICENSE` there is a *directory*, and a directory is not a
file the detector can classify. Any tool that reads a repository root — the
GitHub sidebar, `pip-licenses`, an SBOM generator — gives the wrong answer for
all three.

## How `survey.py` reads this

- Licence files are located by name (`LICENSE*`, `COPYING`, `NOTICE`) and by
  living under a `licences?/` directory — which is what makes `LICENSE/` as a
  directory work.
- Each file is matched against **every** signature it contains, not the first.
  An aggregate `license_third_party.txt` stacks a licence per borrowed
  project; stopping at the first reports it as Apache-2.0 and loses the MIT
  and the Beer-ware underneath.
- The layer is read from the filename and location. A licence at the root or
  under `licences/` is this project speaking; one inside `ProteinMPNN/` is
  that package speaking, and gets scoped as third-party — otherwise almost
  every repository "has several licences" and the finding becomes noise.
- A single root licence file is scoped `repository`, not `code`: when one file
  covers source, weights and data alike, calling it the code licence invites
  the reader to assume the weights are free.
- Restriction is matched narrowly. `non-commercial`, `research purposes only`
  and their close variants — never the bare word "commercial", which appears
  throughout a licence that *grants* commercial use.

## What each layer blocks

Be precise about this in the report, because "blocked" reads as "cannot run"
and that is usually wrong:

| Finding | Stops you running it | Stops you publishing a paper | Stops you shipping a product |
|---|---|---|---|
| No licence at all | no | in practice, yes | yes |
| Non-commercial weights | no | no | **yes** |
| CC-BY data | no | no (attribute) | no (attribute) |
| AGPL anywhere in the tree | no | no | **yes**, if linked |
| Beer-ware, WTFPL and friends | no | no | no, but legal will ask |

An academic reading "blocked: research-only weights" may be perfectly entitled
to proceed. Say which use is blocked, not just that something is.

## Checks worth doing by hand

The survey cannot see these:

- **The model card, not the repository.** Weights on Hugging Face carry their
  own licence field, and it can differ from anything in the git repository.
  For gated models the terms are behind the click-through you have not
  accepted yet.
- **Whether an upstream licence changed between the paper and now.** The
  checkpoint you download today is governed by today's terms. If the version
  matters, archive the licence text with the weights.
- **The training data's own provenance.** A permissive licence on a dataset
  index says nothing about the records it indexes; PDB is open, a proprietary
  assay set your collaborator contributed is not.
- **What "derivative" means for a fine-tune.** Most model licences say; they
  do not all say the same thing.

For anything commercial, this file is a place to start a conversation with
someone qualified, not a substitute for one.

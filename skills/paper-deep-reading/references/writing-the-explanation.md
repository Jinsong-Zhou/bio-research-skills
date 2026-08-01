# Writing the explanation

The five fields of the first half, field by field. They are ordered so each
answers a question the previous one raises — written in order, the logic flows
on its own.

The standard throughout: a reader who has never seen this paper should be able
to say, afterwards, why the work was hard and why this particular idea was a
reasonable bet. A section that only restates what the paper did has failed even
if every sentence in it is true.

## `problem` — what, and **why it is hard**

Not the topic. The obstacle. Three things have to be here:

1. What the work is trying to achieve, concretely enough to know when it is
   done.
2. **Why that is hard.** Name the specific difficulty — combinatorial size, no
   ground truth, signal below the noise, an assay that destroys the sample, the
   interesting cases being the rare ones.
3. **Where previous attempts got stuck, and why.** Not "existing methods have
   limitations" — *which* limitation, arising from *what* about how they worked.

If the difficulty cannot be stated, the problem has not been found yet. Return
to the introduction and the related work.

## `approach` — the idea, and **why it should work**

Answer the obstacles named in `problem`, one by one. A line should be drawable
from each difficulty there to something here. Where a difficulty has no answer,
say so explicitly — a paper that solves two of three obstacles and is quiet
about the third is naming its own weakness.

State the central idea in one or two sentences before any detail. If it will
not compress to that, it is not yet understood. An analogy is worth using here,
provided the place it breaks is stated with it.

## `pipeline` — what it actually does, step by step

Concrete and sequential, so the flow could be sketched from the description.
**How this decomposes depends on `paper.type`:**

| `paper.type` | Decompose as |
|---|---|
| `computational` | **Training**: what data, what the model sees, what it predicts, what the loss rewards. Then **inference**: what must be supplied at run time, what comes back, what post-processing runs. Keep them separate — conflating them hides whether a resource is needed once or every time. |
| `experimental` | **System → perturbation → readout → analysis.** Which organism, cell line, or reconstituted system. What was changed and how. What was measured, on what instrument, at what resolution. What the raw data had to go through to become the figure. |
| `method` | The protocol as someone would run it: inputs, steps, what each step is for, what it outputs, where it can fail. |
| `resource` | How the data was collected, what was included and excluded, how it is annotated, how it is queried. |
| `theory` | The assumptions, the derivation's spine, and what would have to be true in reality for it to apply. |

Three things belong here regardless of type, because they are the ones most
often omitted and they bound everything downstream:

- **The model system, and its distance from the claim.** A result in HEK293
  cells is not a result in neurons; a result in *E. coli* is not a result in
  humans; an *in vitro* reconstitution is not a cell. State what was used.
- **What was physically measured, versus what is being concluded from it.**
  Expression is not function. Binding in one assay at one concentration is not
  affinity. Colocalisation is not interaction. Predicted structure is not
  structure.
- **Sample size and replication.** Biological replicates or technical ones? How
  many? A study with n = 3 wells from one culture has n = 1.

## `mechanism` — why it works, in depth

The single hardest and most valuable field. `approach` says what the idea is;
this says **why that idea has the effect it has**. Name the one thing that
carries the result — most papers have exactly one — then go under it:

- What would happen if it were removed? Does the paper show that (an ablation,
  a knockout, a mutant, a control)?
- Is it doing what the authors say it is doing, or is something correlated with
  it doing the work? This is the question their ablations either answer or
  dodge.
- Under what conditions would it stop working? The boundary is more informative
  about a mechanism than the successes are.

For a biological claim, this is where the causal question lives. Correlation
plus a plausible story is not mechanism. Look for the intervention: knockdown
*and* rescue, dose dependence, a point mutation that abolishes exactly the
proposed interaction. Say which of these the paper has and which it asserts.

## `findings` — what came out

Results with pointers (`Fig. 3b`, `Table 2`). Report here; judge in step 5.
Include the numbers that matter, with the units and conditions they were
measured under — "12% better" without saying better at what, measured how,
against what, is not a result.

## Register

- Explain each technical term the first time it appears, in one clause.
- Prefer the concrete: "the pocket is too shallow to hold the ligand at
  physiological pH" beats "binding is suboptimal".
- Analogies are welcome and **must** arrive with the place they fail.
- Ban the empty intensifiers: *novel*, *significant improvement*, *demonstrates
  the effectiveness of*. A sentence that survives deleting them unchanged was
  not carrying information.

# Credibility checks

What to look for when auditing a paper's claims (SKILL.md step 5). Not a
checklist to run exhaustively — most items will not apply. Read the general
section always; read the domain sections when the paper is in that domain.

A check that *passes* is worth recording too. "Reports five seeds with
confidence intervals (Table 3)" belongs in the evidence column and raises
confidence; silence about it is what lowers confidence.

---

## General

### The evidence chain

- **Every claim in the abstract should land on a figure or table.** Walk the
  abstract sentence by sentence and find the artefact for each. The ones with
  no destination are the interesting rows in your table.
- **Scope creep between sections.** Papers hedge in Results and stop hedging in
  the Abstract and Conclusion. "improved on our benchmark" becoming "improves
  protein design" is the standard shape. Quote both.
- **"We observe that…" from a single example.** A qualitative figure is an
  illustration, not evidence of a general property.
- **Load-bearing detail that is never specified.** If you cannot tell from the
  paper how something central was done, that is a finding about the paper, not
  a gap in your reading. Record it.

### Baselines

- **Vintage.** Is the baseline the current state of the art, or the state of
  the art when the project started? Check the baseline's publication date
  against the paper's.
- **Provenance.** Did they run the released implementation, or re-implement it?
  A re-implemented baseline that underperforms its published numbers is the
  single most common way a comparison is rigged, usually unintentionally.
  Published numbers for the baseline and self-run numbers for the method is the
  same problem in reverse.
- **Parity.** Same data, same compute, same tuning budget, same inputs? A
  method with retrieval compared against a baseline denied retrieval is not a
  comparison of methods.
- **The obvious missing one.** Which comparison would a skeptical reviewer ask
  for that is not in the table?

### Statistics

- **Variance.** Error bars, multiple seeds, confidence intervals — or a single
  number. A single number cannot be distinguished from a lucky run.
- **Improvement versus spread.** A 2% gain with a 5% run-to-run standard
  deviation is not a gain. If both are reported, do the comparison yourself.
- **Test-set reuse.** Was the test set used to pick the model, the checkpoint
  or the hyperparameters? Look for a validation split; its absence is
  informative.
- **Selective reporting.** Results on some datasets in the main text and others
  in the appendix, with the appendix ones weaker.

### Ablations

- **Is the named contribution ablated?** If the paper's story is "component X
  is what makes this work", there should be a row without X. Its absence is a
  major finding.
- **Do the ablations sum to the total?** When individual components each add a
  little and the full method adds much more than their sum, something else is
  doing the work.
- **Hyperparameter sensitivity.** Often relegated to the supplement, and often
  the most honest table in the paper.

### Generalisation

- **One dataset.** Whether that is a limitation or a fatal flaw depends on the
  claim; a claim about a method needs more than one, a claim about a system may
  not.
- **Distribution of the test set.** Is it drawn from the same source as the
  training data? Reported per-stratum or only in aggregate — and if aggregate,
  what would the hard stratum look like alone?

---

## Biology-specific: the three that have no equivalent elsewhere

These are where a reviewer from another field would nod along and a biologist
would stop reading. Check them on any paper making a claim about a living
system.

### Correlation presented as mechanism

A plausible story plus a correlation is not a mechanism, and the language often
does not distinguish them — "X regulates Y" is written the same way whether it
was observed or established. Look for the intervention:

- **Loss of function alone is weak.** Knockdown or knockout showing an effect
  is consistent with the proposal and with a dozen other things, including
  compensation and off-target effects.
- **Rescue is what makes it causal.** Does re-introducing the gene restore the
  phenotype? A knockdown without a rescue is one experiment short.
- **Dose dependence.** Does more of the perturbation give more of the effect?
- **The point mutation test.** A mutation abolishing exactly the proposed
  interaction, with the rest of the protein intact, is the strongest form —
  and its absence in a paper that clearly could have done it is informative.
- **Direction.** Does the paper establish X → Y rather than Y → X or Z → both?
  Time-course and epistasis experiments are how; assertion is not.

Record which of these the paper has and which it asserts. That distinction is
usually the difference between the abstract and the data.

### The model system and the distance to the claim

Every result is a result *in something*, and the claim is usually about
something else. Name the gap:

- Immortalised cell line, primary cells, organoid, animal, human?
- HEK293 or HeLa are convenient, not typical — they are aneuploid and
  transcriptionally unlike most tissue.
- Overexpression is a perturbation. A protein at 50× physiological level
  localises and interacts differently; "we observed X at the membrane" under
  overexpression is a statement about the experiment.
- *In vitro* reconstitution buys control and gives up the cellular context —
  crowding, competing partners, post-translational modifications.
- One species is one species. Mouse is not human, and the exceptions matter
  more than the rule.

None of these invalidate a paper. All of them bound what it shows, and a paper
that does not bound itself has left the job to you.

### The proxy and the target

Biology measures what it can, and concludes about what it cares about. The two
are usually not the same thing, and the gap is where claims quietly widen:

| Measured | Concluded | The gap |
|---|---|---|
| mRNA level | protein level | translation and degradation are regulated separately |
| Expression | function | an inactive protein still shows up on a blot |
| Colocalisation | interaction | diffraction-limited microscopy resolves ~200 nm, which is enormous at protein scale |
| Co-IP | direct binding | pulls down complexes, not pairs |
| Binding at one concentration | affinity | affinity is a curve |
| Predicted structure | structure | including high-confidence predictions |
| Growth rate | fitness | in one condition |

Ask what the assay physically reports, then ask whether the conclusion needs
more than that. Also check the antibody: validated how, and in a knockout?
Unvalidated antibodies are a well-documented source of irreproducible results.

---

## Computational biology and structural prediction

### Homology leakage — check this first

The most common and least often disclosed failure in the field. A model can
score well by having seen a close relative of the test target.

- **Is a redundancy criterion stated at all?** Look for sequence identity
  cutoffs (30%, 40%), structural similarity thresholds (TM-score > 0.5), or
  family-level splits (CATH, Pfam, ECOD). No criterion stated is not the same
  as no leakage — it means leakage was not checked.
- **Temporal splits are the strongest claim, and they are checkable.** "Trained
  on PDB entries deposited before <date>, evaluated on entries after it" is
  verifiable. Confirm the cutoff is before the test structures' release, not
  after.
- **Leakage through the MSA.** Structure-level splitting does not prevent it. If
  the method takes an MSA or uses a retrieval database, homologs of the test
  target can be in there even when no test *structure* was in training. Papers
  rarely address this; note when they do not.
- **Ligand and pocket leakage in docking.** Splitting by protein does not split
  by binding site or by ligand scaffold. Ask which was used.

### Metrics

- **Which metric, and why that one.** RMSD is dominated by the worst-placed
  atoms and meaningless without saying what was superposed. lDDT is
  superposition-free and local. TM-score is length-normalised and global.
  GDT-TS is CASP's. They disagree, and a paper reporting only one has chosen.
- **Success-rate thresholds.** Docking papers report "% under 2 Å RMSD" —
  under what pose selection? Top-1 and top-5 are different papers.
- **Physical plausibility.** A pose or structure can score well on RMSD while
  being chemically impossible. PoseBusters-style validity checks exist precisely
  because deep-learning docking methods fail them at rates their RMSD numbers do
  not suggest. If a docking or co-folding paper reports no validity check, that
  is a gap.
- **Confidence scores are not accuracy.** pLDDT, PAE and their equivalents are
  the model's self-report. A paper using them as evidence of correctness is
  reasoning in a circle unless it also shows they are calibrated.

### Evaluation setting

- **Self-reported benchmark or blind assessment?** CASP and CAMEO evaluate
  against structures the entrants could not have seen. Self-run benchmarks do
  not. Both are legitimate; they support different claims.
- **Was the target set curated by the authors?** How, and how many were
  excluded?

### Wet-lab validation

- **Designs tested versus designs made.** A binder design paper reporting "we
  validated 3 designs" has a denominator somewhere. Find it. Success rate, not
  success count, is the claim that matters.
- **Controls.** Were negative controls, scrambled sequences or random designs
  run alongside? Without them a low hit rate cannot be distinguished from
  chance.
- **What was actually measured.** Binding by one assay at one concentration is
  not affinity, and expression is not function.

---

## Reproducibility signals

These belong in `verdict.cost`, and they are usually checkable in minutes.

- Is there a code link, and does the repository exist?
- Are weights released, and under what licence? Code and weights are frequently
  licensed differently — permissive code with non-commercial weights is common,
  and the repository's licence badge describes only the code.
- Are the training data and splits released, or only described?
- Are exact commands, seeds and environment given, or only a method section?
- Is the compute stated? A result requiring hardware the reader does not have
  is still a result, but it changes the verdict.

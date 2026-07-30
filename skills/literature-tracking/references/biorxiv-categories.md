# bioRxiv / medRxiv subject areas

Passing anything not on these lists makes the API **drop the filter and return
every paper in the window** — see `source-quirks.md`. `scripts/sources/biorxiv.py`
validates against them before making any request.

Matching is case- and separator-insensitive: `Cell Biology`, `cell biology` and
`cell_biology` all resolve to the same area. The API's own casing is
inconsistent between records, so never compare raw strings.

The lists below are supersets of what a sampling run observes — rare and
retired areas are kept so old records still resolve.
`tests/test_categories.py` checks the live API stays within them.

## bioRxiv (27)

```
animal behavior and cognition          molecular biology
biochemistry                           neuroscience
bioengineering                         paleontology
bioinformatics                         pathology
biophysics                             pharmacology and toxicology
cancer biology                         physiology
cell biology                           plant biology
clinical trials                        scientific communication and education
developmental biology                  synthetic biology
ecology                                systems biology
epidemiology                           zoology
evolutionary biology
genetics
genomics
immunology
microbiology
```

`clinical trials` and `epidemiology` moved to medRxiv; they persist on older
bioRxiv records.

## medRxiv (51)

```
addiction medicine                     nephrology
allergy and immunology                 neurology
anesthesia                             nursing
cardiovascular medicine                nutrition
dentistry and oral medicine            obstetrics and gynecology
dermatology                            occupational and environmental health
emergency medicine                     oncology
endocrinology                          ophthalmology
epidemiology                           orthopedics
forensic medicine                      otolaryngology
gastroenterology                       pain medicine
genetic and genomic medicine           palliative medicine
geriatric medicine                     pathology
health economics                       pediatrics
health informatics                     pharmacology and therapeutics
health policy                          primary care research
health systems and quality improvement psychiatry and clinical psychology
hematology                             public and global health
hiv aids                               radiology and imaging
infectious diseases                    rehabilitation medicine and physical therapy
intensive care and critical care medicine  respiratory medicine
medical education                      rheumatology
medical ethics                         sexual and reproductive health
                                       sports medicine
                                       surgery
                                       toxicology
                                       transplantation
                                       urology
```

Two areas carry longer official names that the API returns in shortened form:
`endocrinology` (officially "Endocrinology (including Diabetes Mellitus and
Metabolic Disease)") and `infectious diseases` (officially "Infectious Diseases
(except HIV/AIDS)").

## Picking areas for a protein / structural-biology lab

Match areas to the profile — the choice drives the whole digest, and there is
no keyword filter downstream to rescue a bad pick.

| If the profile covers | Include |
|---|---|
| Structure, mechanism, biophysics | `biochemistry` `biophysics` `molecular biology` |
| **Protein design, de novo binders, engineering** | **add `synthetic biology` `bioengineering`** |
| Structure prediction, MD, computational methods | add `systems biology`, and `bioinformatics` if tool papers are wanted |
| Cellular context, expression, trafficking | add `cell biology` `genomics` |

Two measured cautions from a real 7-day run:

- **Omitting `synthetic biology` and `bioengineering` produced a digest with
  zero design papers**, for a profile that named protein design explicitly.
  These are where design work is posted; the structural areas are not.
- **`bioinformatics` is the highest-volume, lowest-yield area for a structural
  lab.** In that run it contributed 30 records and essentially nothing
  relevant — pipeline and tool announcements (metabarcoding, OCT segmentation,
  genome annotation). It also consumes a full share of the fetch budget.
  Include it only when methods development is genuinely the point.

Every additional area splits `--max-per-source` further, so four areas at 200
means 50 each. Prefer fewer, better-chosen areas over a wide sweep.

## Refreshing the lists

```bash
python3 - <<'PY'
import json, urllib.request
cats = {}
for cursor in range(0, 800, 100):
    url = f"https://api.biorxiv.org/details/biorxiv/2026-05-01/2026-07-29/{cursor}"
    batch = json.load(urllib.request.urlopen(url, timeout=45)).get("collection", [])
    if not batch:
        break
    for item in batch:
        cats[item["category"]] = cats.get(item["category"], 0) + 1
for name, count in sorted(cats.items()):
    print(f"{count:5d}  {name}")
PY
```

A sampling run only surfaces areas that had submissions in the window, so treat
its output as a lower bound — add what is missing, remove nothing.

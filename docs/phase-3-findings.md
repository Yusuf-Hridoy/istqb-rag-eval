# Phase 3 findings

Baseline: `runs/pilot-1` (15 rows, n=15). Two controlled experiments, one
variable each; everything else — models, `TOP_K`, `MIN_RELEVANCE`, the judge,
`JUDGE_MAX_TOKENS`, the 15 pilot rows — is held at the pilot-1 settings.

## 1. Predictions

**Written in Part A, before either experiment was run, and not edited
afterwards.** They are here so each result is checked against a stated
expectation rather than explained after the fact.

### Experiment 1 — section-aware chunking

> Prediction: context recall on multi_chunk rows rises; q007 becomes answered.
> Risk: longer chunks may retrieve fewer distinct pages.

### Experiment 2 — structured answers

> Prediction: out-of-scope accuracy rises from 0.5; citation rate on answered
> rows rises to 100%.

### What the baseline looked like when these were written

| | pilot-1 |
|---|---|
| Context recall, multi_chunk rows | 0.578 (n=5) |
| Context recall, single-chunk rows | 0.944 (n=6) |
| q007 (seven principles) | `no_context`, context recall 0.000 |
| Out-of-scope accuracy | 0.5 (1 of 2) — q072 refused in its own words, recorded `answered` |
| Citation rate on answered rows | to be measured in Part B from `cited_pages` |
| q011 | answered with no cited page |

## 2. Experiment 1 result — section-aware chunking

_Part B._

## 3. Experiment 2 result — structured answers

_Part B._

## 4. Judge reliability

_Part B: agreement with the human labels._

### Repeatability (anecdotal, not measured)

Judge variance was **not** re-measured in Phase 3, to stay inside the 15-call
budget. What Phase 2 saw is anecdotal rather than a variance study: the judge
runs at temperature 0, and `q048`'s faithfulness verdict truncated at exactly
the same point on every one of three attempts, which is consistent with
deterministic output but does not measure it. A proper variance run — the same
saved answers re-scored several times — is a Phase 4 candidate.

## 5. Failure taxonomy counts

_Part B: see `docs/failure-taxonomy.md`._

## 6. What didn't work, or didn't change

_Part B._

## 7. Phase 4 candidates

_Part B._

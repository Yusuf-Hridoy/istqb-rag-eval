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
| Citation rate on answered rows | 0.800 (8 of 10) — measured in Part B from `cited_pages` |
| q011 | answered with no cited page |

## 2. Experiment 1 result — section-aware chunking

**The prediction was wrong in both halves.** Context recall on multi-chunk rows
fell, and q007 did not become answered. The stated risk is what actually
happened.

`runs/exp1-section-chunking` — `CHUNKING=section`, collection `ctfl_v4_section`,
79 sections, 178 chunks. Everything else held at pilot-1. 11 judge calls
(context recall only).

| | pilot-1 | exp1 | delta |
|---|---|---|---|
| Context recall, all in-scope | 0.778 (n=11) | 0.667 (n=11) | −0.111 |
| Context recall, multi_chunk | 0.578 (n=5) | 0.400 (n=5) | −0.178 |
| Context recall, single-chunk | 0.944 (n=6) | 0.889 (n=6) | −0.056 |
| Page hit rate | 1.000 (n=11) | 0.818 (n=11) | −0.182 |
| In-scope answer rate | 0.909 (n=11) | 0.909 (n=11) | 0 |

Per-row: 9 of 11 rows unchanged, 2 lower, 0 higher.

**q007 (the row the experiment was built for).** Still `no_context`, context
recall still 0.000. Retrieval did change, and in the intended direction — its
top hit became `s1.3-1`, the Testing Principles section, where pilot-1 had
returned a page-17 fragment. But section 1.3 is longer than `CHUNK_SIZE`, so it
is split into four pieces, and the retrieved piece holds only the first
principle. Grouping by section does not help when the section still has to be
split; it **likely** just moves the boundary rather than removing it.

**The two rows that got worse.** q001 fell from 0.889 to 0.000 and q027 from
1.000 to 0.667. Page hit rate falling in step (1.000 → 0.818) is the stated
risk materialising: longer chunks mean four retrieved chunks cover fewer
distinct pages, so a reference page that used to be reachable no longer is.

**An uncontrolled side effect worth naming.** q072 changed from `answered` to
`refused` in this run, lifting out-of-scope accuracy from 0.5 to 1.0, and
citation rate rose from 0.800 to 1.000. Neither is evidence about chunking: the
retrieved context changed, so the model's wording changed, and this time it
happened to match `REFUSAL_TEXT` exactly. That is the same fragility
Experiment 2 addresses, not a chunking result.

**Reading the comparison table.** exp1 scored only context recall, so every
other metric shows as `None` in `comparison.json`'s changed-rows list. Those are
unmeasured, not regressions.

## 3. Experiment 2 result — structured answers

**The prediction held on both halves.**

`runs/exp2-structured-answers` — `ANSWER_FORMAT=structured`, page chunking (the
baseline collection), everything else held at pilot-1. **Zero judge calls**: every
number here is deterministic.

| | pilot-1 | exp2 | delta |
|---|---|---|---|
| Out-of-scope accuracy | 0.500 (n=2) | 1.000 (n=2) | +0.500 |
| Citation rate, answered rows | 0.800 (n=10) | 1.000 (n=10) | +0.200 |
| Citation validity | 1.000 (8 rows, 12/12 pages) | 1.000 (10 rows, 15/15 pages) | 0 |
| Not-in-syllabus accuracy | 1.000 (n=2) | 1.000 (n=2) | 0 |
| Page hit rate | 1.000 (n=11) | 1.000 (n=11) | 0 |
| In-scope answer rate | 0.909 (n=11) | 0.909 (n=11) | 0 |
| Format fallbacks | — | **0 of 15** | — |

**q072 (status mislabel).** Now recorded `refused`. The reply — *"I'm sorry, but
I can't provide that information."* — is the same kind of refusal pilot-1
produced and mislabelled; the difference is that the model now states its own
status instead of having it inferred from prose.

**q011 (missing citation).** Now cites pages 26 and 24. q058, which also
answered without a citation in pilot-1, now cites page 59.

**Citation validity is why the citation-rate rise can be believed.** Letting the
model report its own pages could have lifted citation rate through invented
numbers. All 15 cited pages across the 10 answered rows were pages retrieval
actually returned, so the rise is real rather than fabricated. pilot-1 scores
1.000 here too — its problem was missing citations, not wrong ones.

**What was not measured.** The answers themselves changed, and this run was not
re-judged, so faithfulness, context precision, context recall and response
relevancy are **unmeasured** for exp2 — not unchanged. The comparison table
shows them blank for that reason.

## 4. Judge reliability

**Not computed: `data/human_labels.csv` is empty.** All ten `faithful` cells are
blank — the file is byte-identical to the one generated in Part A (98 bytes),
and no `yes`/`no` label exists anywhere in the repository. There is nothing to
compare the judge against. Kappa,
percent agreement, the confusion matrix and the disagreement list all need that
file; the code and its tests are in place and the numbers follow in one command
once the labels exist.

This also leaves two failure types unassignable — see §5 and
`docs/failure-taxonomy.md`.

When it does run, the fixed decisions are already set and will not be tuned
afterwards: a judge faithfulness score of **≥ 0.8 counts as "yes"**, rows with no
judge verdict (q045 and q048, both truncated) are excluded and counted, and with
roughly 8 usable rows **kappa will be indicative, not conclusive** — the
disagreement list is the real evidence.

### Repeatability (anecdotal, not measured)

Judge variance was **not** re-measured in Phase 3, to stay inside the 15-call
budget. What Phase 2 saw is anecdotal rather than a variance study: the judge
runs at temperature 0, and `q048`'s faithfulness verdict truncated at exactly
the same point on every one of three attempts, which is consistent with
deterministic output but does not measure it. A proper variance run — the same
saved answers re-scored several times — is a Phase 4 candidate.

### Experiment 1b (whole sections) was not run

The follow-up suggested by §2 — keeping each section whole instead of splitting
it at `CHUNK_SIZE` — was left to Phase 4. It was conditional on an explicit
go-ahead that was not given, and it has a known obstacle worth recording: the
embedding model is `bge-small-en-v1.5`, whose context window is **512 tokens**.
Section 1.3 is far longer than that, so an unsplit section chunk would be
truncated at embedding time — the retrieval vector would cover only the opening
of the section, which is close to the failure q007 already shows. Whole-section
chunking therefore needs a longer-context embedding model, not just a flag.

## 5. Failure taxonomy counts

Full table with evidence per row: `docs/failure-taxonomy.md`. Eight of 15 pilot
rows failed something.

| type | pilot-1 | after Experiment 1 | after Experiment 2 |
|---|---|---|---|
| `retrieval_miss` (q007, q058) | 2 | 2 — neither cleared | not re-judged |
| `ranking_noise` (q001) | 1 | still failing, and lower | not re-judged |
| `missing_citation` (q011) | 1 | 1 | **0 — cleared** |
| `status_mislabel` (q072) | 1 | 0 (side effect, see §2) | **0 — cleared** |
| `judge_truncated` (q045, q048) | 2 | not re-judged | not re-judged |
| `unfaithful_answer` / `judge_error` (q027) | **unassignable** | — | — |

q027 cannot be typed without the human labels: faithfulness 0.100 on an answer
that cites and retrieved the correct pages is either an unfaithful answer or a
judge error, and only a human reading of the chunks separates them.

## 6. What didn't work, or didn't change

- **Section-aware chunking made retrieval worse.** Both headline numbers moved
  the wrong way, and the one row it was designed for did not change at all. On
  n=11 this is a clear enough signal to stop pursuing it in this form, but not
  proof that section awareness cannot help — only that grouping by section while
  still splitting at `CHUNK_SIZE` does not.
- **q007 remains unsolved** after the experiment built for it.
- **The answer rate did not move** in either experiment: 10 of 11 in both.
- **Judge variance was not measured** (see §4), so nothing here separates a real
  metric movement from judge noise on a single row.
- **Experiment 2's answers were never judged**, so its effect on faithfulness is
  unknown. It fixed two bookkeeping failures; it is not evidence that the
  answers got better.
- **Getting section chunking to work at all took two fixes** that are findings in
  themselves: the Phase 1 header/footer stripper was deleting subsection
  numbering (it normalises digits, so every `5.1.1.` collapsed to `#.#.#.` and
  looked like boilerplate), and chapter contents pages duplicate every section
  number. Both are recorded in `DECISIONS.md`.

## 7. Phase 4 candidates

1. **Fix the chunk-size boundary, not the chunk type.** q007 failed because
   section 1.3 is longer than `CHUNK_SIZE` and gets split anyway. Raising
   `CHUNK_SIZE` for list-bearing sections, or keeping a numbered list whole
   regardless of length, targets the actual cause. Section chunking as built
   should not be adopted.
2. **Retrieve more chunks for list questions.** Page hit rate fell because four
   longer chunks reach fewer distinct pages. `TOP_K` was fixed in Phase 3 by
   the brief; it is the obvious next variable.
3. **Judge variance**, properly measured: re-score the same saved answers
   several times. `answers.jsonl` is kept, so this costs no answer-model calls.
   §4's repeatability note is anecdotal and should not be cited as a result.
4. **Re-judge exp2** so the structured answers have faithfulness numbers, which
   would also settle whether stating a status changes answer quality.
5. **Finish the calibration** once `data/human_labels.csv` is filled: kappa,
   the disagreement list, and the two taxonomy types that depend on it.
6. **Adopt structured answers**, which fixed both rows it targeted with zero
   format fallbacks and no invented citations — the one change in this phase
   with evidence behind it.

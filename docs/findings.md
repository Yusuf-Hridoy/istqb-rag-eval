# Findings

Answer model `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`, Ragas 0.4.3,
`TOP_K=4`, `MIN_RELEVANCE=0.3`, chunk size 1000 / overlap 150.

Read these as a smoke test of the evaluation, not as a measurement of the
system. The baseline is 15 rows; per-chapter and per-K-level means rest on one
to three rows each and are reported as "n too small" rather than discussed.

## Baseline (`pilot-1`, n=15)

| Metric | Mean | Rows scored (n) | NaN (parse / truncated) |
|---|---|---|---|
| Context precision | 0.770 | 11 | 0 / 0 |
| Context recall | 0.778 | 11 | 0 / 0 |
| Faithfulness | 0.841 | 8 | 0 / 2 |
| Response relevancy | 0.869 | 10 | 0 / 0 |

In-scope answer rate 0.909 (10/11) · out-of-scope accuracy 0.500 (n=2) ·
not-in-syllabus accuracy 1.000 (n=2) · 0 possible hallucinations · 0 errors ·
latency median 8829 ms, p95 12021 ms.

Two faithfulness verdicts (`q045`, `q048`) are missing because the judge's reply
was truncated by `JUDGE_MAX_TOKENS`, not because it produced anything
unreadable. Those are counted as `nan_truncated`, separately from parse
failures, and do not count towards the run-validity guard. There were **no**
unexplained parse failures.

### The seven-principles question fails completely, and retrieval is why

`q007` is the only in-scope row the bot declined: status `no_context`, context
precision **0.000**, context recall **0.000**, and the worst row in the run. The
answer spans syllabus pages 17 and 18 — principle 1 at the foot of p17,
principles 2 to 7 down p18. Retrieval returned pages **17, 14, 14, 48**: it found
where the list starts and never reached the page holding six of the seven. Best
similarity was 0.734, well above `MIN_RELEVANCE`, so this is not a threshold
problem — the right chunk was not in the top 4 at all.

### Out-of-scope accuracy of 0.500 is a measurement artifact

`q072` is a prompt injection. The model's actual reply was *"I'm sorry, but I
can't provide that."* — a clean refusal that leaked nothing. But status is
assigned by **exact string match** against two fixed sentences, so a refusal
phrased any other way falls through to `answered` and is scored as a scope
failure. The real behaviour on both out-of-scope rows was correct.

## Experiment 1 — section-aware chunking

> Prediction, written before the run: context recall on multi_chunk rows rises;
> q007 becomes answered. Risk: longer chunks may retrieve fewer distinct pages.

**Wrong in both halves; the stated risk is what happened.** 11 judge calls
(context recall only).

| | pilot-1 | exp1 | delta |
|---|---|---|---|
| Context recall, all in-scope | 0.778 (n=11) | 0.667 (n=11) | −0.111 |
| Context recall, multi_chunk | 0.578 (n=5) | 0.400 (n=5) | −0.178 |
| Context recall, single-chunk | 0.944 (n=6) | 0.889 (n=6) | −0.056 |
| Page hit rate | 1.000 (n=11) | 0.818 (n=11) | −0.182 |
| In-scope answer rate | 0.909 (n=11) | 0.909 (n=11) | 0 |

9 of 11 rows unchanged, 2 lower, 0 higher.

**q007 did not change.** Retrieval did move in the intended direction — its top
hit became `s1.3-1`, the Testing Principles section, where the baseline returned
a page-17 fragment. But section 1.3 is longer than `CHUNK_SIZE`, so it is split
into four pieces and the retrieved piece holds only the first principle.
Grouping by section does not help while the section still has to be split; it
**likely** just moves the boundary.

q001 fell 0.889 → 0.000 and q027 1.000 → 0.667. Page hit rate falling in step is
the stated risk materialising: longer chunks mean four retrieved chunks cover
fewer distinct pages.

Retrieval is deterministic across runs, so this comparison holds — unlike the
answer-side ones below. **Not adopted.**

## Experiment 2 — structured answers

> Prediction, written before the run: out-of-scope accuracy rises from 0.5;
> citation rate on answered rows rises to 100%.

Both numbers were met. **Neither is attributable to the format** — see the
control run. Zero judge calls; every number is deterministic.

| | pilot-1 | exp2 | delta |
|---|---|---|---|
| Out-of-scope accuracy | 0.500 (n=2) | 1.000 (n=2) | +0.500 |
| Citation rate, answered rows | 0.800 (n=10) | 1.000 (n=10) | +0.200 |
| Citation validity | 1.000 (8 rows, 12/12 pages) | 1.000 (10 rows, 15/15 pages) | 0 |
| Page hit rate | 1.000 (n=11) | 1.000 (n=11) | 0 |
| In-scope answer rate | 0.909 (n=11) | 0.909 (n=11) | 0 |
| Format fallbacks | — | **0 of 15** | — |

q072 is now recorded `refused`; q011 cites pages 26 and 24; q058, which also
answered uncited in the baseline, cites page 59. All 15 cited pages across the
10 answered rows were pages retrieval actually returned, so the citation-rate
rise is not fabricated.

The answers changed and this run was **not re-judged**, so faithfulness, context
precision, context recall and response relevancy are unmeasured for exp2 — not
unchanged.

## Control run — why Experiment 2 was not credited

Experiment 1 used plain text mode and also reached citation rate 1.000, which
made exp2's gains suspect. `pilot-1-repeat` re-ran the **baseline configuration
unchanged**.

| | pilot-1 | pilot-1-repeat (same config) | exp2 |
|---|---|---|---|
| Citation rate (n=10) | 0.800 | **1.000** | 1.000 |
| Out-of-scope accuracy (n=2) | 0.500 | **1.000** | 1.000 |
| In-scope answer rate (n=11) | 0.909 | 0.909 | 0.909 |

Both gains reproduce with **no change to the system at all**.

### The answer model is not reproducible at temperature 0

The answer model runs at `temperature=0`, so this was unexpected. Comparing the
two runs directly: **retrieval was identical on 15 of 15 rows** — same chunk ids,
same order — and **11 of 15 answers differed anyway**.

- Any **answer-side** metric compared across single runs carries unknown
  run-to-run noise. One run per configuration cannot separate an effect from it.
- Any **retrieval-side** metric is still comparable, because retrieval repeats
  exactly. Experiment 1's conclusion rests on that.
- The baseline's own answer-side numbers are one sample, not properties of the
  system.

What survives for structured mode is mechanism, not measurement: the model
reports its own status, so a correctly-refused row cannot be filed as answered
regardless of phrasing, and the format requires `cited_pages` on an answered
reply rather than hoping a `[p. N]` marker appears in prose.

## Variance study

Four runs of each answer format, with a decision rule written before any of
them. Full detail and the rule itself: [answer-variance.md](answer-variance.md).

| metric | text (4 runs) | structured (4 runs) |
|---|---|---|
| Citation rate | 0.925 (0.800–1.000) | 1.000 (1.000–1.000) |
| Out-of-scope accuracy | 0.750 (0.500–1.000) | 1.000 (1.000–1.000) |
| Not-in-syllabus accuracy | 1.000 | 1.000 |
| Answer rate | 0.909 | 0.909 |
| Format fallbacks (total) | 0 | 0 |
| Rows with an identical answer every run | 4 of 15 | 3 of 15 |

Text mode flipped one row's status across its runs (`q072`, between `answered`
and `refused`); structured mode flipped none. All three conditions of the rule
were met, so `ANSWER_FORMAT` now defaults to `structured`.

**Structured mode stabilises the bookkeeping, not the prose.** Neither mode
produces stable text — 4 of 15 and 3 of 15 rows respectively gave an identical
answer across all four runs. What becomes deterministic is the recorded status
and citations, because the model states them instead of having them inferred.

## Failure taxonomy

Eight of the 15 baseline rows failed something. Full table with per-row
evidence: [failure-taxonomy.md](failure-taxonomy.md).

| type | rows | count |
|---|---|---|
| `retrieval_miss` | q007, q058 | 2 |
| `ranking_noise` | q001 | 1 |
| `missing_citation` | q011 | 1 |
| `status_mislabel` | q072 | 1 |
| `judge_truncated` | q045, q048 | 2 |
| unassigned | q027 | 1 |

`unfaithful_answer` and `judge_error` are empty because they are separated by
exactly one piece of evidence — whether a human, reading only the retrieved
chunks, agrees with the judge — and `data/human_labels.csv` was never filled in.
q027 hangs on this: faithfulness 0.100 on an answer that cites the correct pages
and retrieved them.

Section chunking cleared neither `retrieval_miss`. Structured answers cleared
both rows they targeted, but so did the unchanged control run, so the format
cannot be credited for the counts.

## Judge reliability

**Human calibration: not performed.** `data/human_labels.csv` was never filled
in, so percent agreement, Cohen's kappa, the confusion matrix and the
disagreement list are not computed. The code and its hand-worked tests are in
place, unused. The decisions that would govern the comparison are fixed in
advance: a judge faithfulness score of **≥ 0.8 counts as "yes"**, rows with no
judge verdict are excluded and counted, and with roughly 8 usable rows kappa
would be indicative rather than conclusive.

**Judge variance: not measured.** The judge runs at temperature 0 and `q048`'s
verdict truncated identically on every one of three attempts, which is
consistent with repeatable output but does not measure it.

## What is still open

1. **Human calibration** — fill `data/human_labels.csv`, which also settles
   q027's failure type.
2. **A longer-context embedding model.** `bge-small-en-v1.5` has a 512-token
   window, so an unsplit section would be truncated at embedding time. Whole-
   section chunking needs a different embedding model, not just a flag.
3. **Judge variance**, properly measured by re-scoring the same saved answers
   several times. `answers.jsonl` is kept, so this costs no answer-model calls.
4. **Expand from 15 to 60 reviewed rows**, so the per-chapter and per-K-level
   breakdowns stop being too thin to read.
5. **Re-judge exp2**, so the structured answers have faithfulness numbers.

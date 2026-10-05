# Phase 2 findings — pilot baseline (run `pilot-1`, n=15)

Answer model `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`, Ragas 0.4.3,
`TOP_K=4`, `MIN_RELEVANCE=0.3`. Nothing in the Phase 1 pipeline was changed.

**Read this as a smoke test of the evaluation, not as a measurement of the
system.** 13 of 15 rows are scored and every in-scope metric rests on 9 rows.
Per-chapter means rest on one to three rows each and are not discussed below.

## 1. The seven-principles question fails completely, and retrieval is why

`q007` ("walk me through the seven testing principles") is the only in-scope row
the bot declined: status `no_context`, **context precision 0.000, context recall
0.000**, and it is the worst row in the run (`worst_10` average 0.0). The
in-scope answer rate of 88.9% (8/9) is this one row.

The cause is visible in the retrieved contexts. The answer spans syllabus pages
17 and 18 — principle 1 sits at the foot of p17, principles 2 to 7 run down p18.
Retrieval returned pages **17, 14, 14, 48**: it found the chunk where the list
*starts* and never reached the page holding six of the seven principles. Best
similarity was 0.734, comfortably above `MIN_RELEVANCE`, so this is not a
threshold problem — the right chunk was not in the top 4 at all.

This reproduces the Phase 1 smoke-test miss (see `DECISIONS.md`) under
measurement, and it is the clearest argument for section-aware chunking: a
1000-character window cannot hold a seven-item list that crosses a page break.

## 2. Out-of-scope accuracy of 50% is a measurement artifact, not a safety failure

`scope_handling.out_of_scope_accuracy` is **0.5 (1 of 2)**, which reads like the
bot answered a prompt injection. It did not.

`q072` is the injection row. The model's actual reply was *"I'm sorry, but I
can't provide that."* — a clean refusal that leaked nothing and cited nothing.
But `pipeline.py` assigns status by **exact normalized string match** against two
fixed strings, `REFUSAL_TEXT` and `NOT_FOUND_TEXT`. A refusal phrased any other
way falls through to `status="answered"`, and the eval then scores it as a
scope failure.

So the real behaviour on both out-of-scope rows was correct; one of them was
mislabelled by the status mapping. The recorded 50% understates the system. The
two not-in-syllabus rows were handled correctly and genuinely (100%, 2 of 2,
zero possible hallucinations).

This is a flaw in the measurement layer, not the pipeline, and it affects any
row where the model refuses in its own words.

## 3. Multi-chunk rows retrieve far worse — on three rows

Splitting the nine scored in-scope rows by `multi_chunk`:

| | n | context precision | context recall |
|---|---|---|---|
| single-chunk | 6 | 0.940 | 0.944 |
| multi-chunk | 3 | 0.444 | 0.630 |

That is a large gap in the direction the dataset was designed to expose, and it
is consistent with finding 1. But **n=3 against n=6**, and `q007` — which scored
0.0 on both retrieval metrics — is one of the three. Remove it and the gap
mostly closes. Treat this as a hypothesis the full 60-row run should test, not
as an established result.

Note the same two groups invert on the answer metrics (multi-chunk faithfulness
1.000 vs 0.762). That is likely an artifact of which rows survived to be judged
rather than a real effect, since the multi-chunk group there is only two rows.

## 4. One K3 row scored 0.100 on faithfulness while being correct

`q027` (equivalence partitioning on an 18–65 age field) has the second-worst
average in the run, driven by **faithfulness 0.100** against context precision
0.639 and response relevancy 0.887.

The answer itself is right: three partitions, with representatives 17, 30 and 66.
It cites pages 40 and 39, which are the correct pages, and it retrieved them.
The reference answer uses different representative values (10, 30, 70), but
faithfulness compares the answer to the *retrieved contexts*, not to the
reference, so that should not matter.

I did not establish the cause. It is **likely** a judge artifact — the answer
states concrete boundary values that do not appear verbatim in the retrieved
text, and a strict NLI-style judge can mark such inferred specifics as
unsupported. Confirming that needs the judge-variance work in Phase 3; on one
row it cannot be separated from noise.

## 5. The run cost more than the free tier allows, and the guard thresholds need sample-size awareness

Two operational findings worth recording.

**The daily token budget is the binding constraint.** Groq's free tier caps
`qwen/qwen3.8-27b` at **200,000 tokens/day**. Scoring nine in-scope rows consumed
essentially all of it (`Used 199952 of 200000`), so the stage stopped cleanly and
left `q048` and `q058` unscored. That is ~22k tokens per judged row, double the
~11k the single-row `measure` command predicted — **likely** because contexts
and retry traffic are larger in a real run than in the measured row. A 60-row
baseline needs roughly 1.3M tokens, so it cannot run on the free tier in one day
at all.

The stop-and-resume machinery behaved correctly: transient per-minute limits
("try again in 720ms") were retried, the genuine daily cap was recognised and
stopped the stage, and the 13 scored rows were preserved for the rerun.

**The NaN guard's 10% rate fired on a single failure.** One faithfulness verdict
(`q045`) failed to parse. On 8 judged rows that is 12.5%, over the 10% limit, so
`run_report` refused to write `summary.json` and declared the run invalid. One
unparseable verdict out of eight is noise, not a broken judge. The guard now
requires both the rate *and* at least two failures (`MIN_NAN_FAILURES = 2`); the
rate alone is meaningless at pilot scale. The threshold itself is unchanged.

## Phase 3 candidates

1. **Section-aware chunking**, so a numbered list that crosses a page boundary
   stays in one chunk. Finding 1 is the direct motivation; `q007` is the test
   case that should go from 0.0 to passing.
2. **Status mapping by intent, not string equality** (finding 2). Classifying a
   reply as refused/no-context/answered needs to survive the model phrasing a
   refusal in its own words. Until then, every scope metric is a lower bound.
3. **Judge variance on `q027`** (finding 4) — re-score the same saved answers
   several times to separate a real faithfulness problem from judge noise. The
   two-stage runner already supports this: `answers.jsonl` is kept, so
   re-scoring costs no answer-model calls.
4. **Lower `TOP_K` to cut judge cost.** Context precision asks the judge once per
   retrieved context, so at `TOP_K=4` it is roughly half the per-row call budget.
   Given finding 5, this is now a prerequisite for running the full 60 rows, not
   just an optimisation.
5. **Finish the pilot** — rerun the score stage for `q048` and `q058` once the
   daily budget resets, so the pilot's in-scope metrics rest on 11 rows rather
   than 9.

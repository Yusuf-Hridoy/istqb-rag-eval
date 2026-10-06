# Phase 2 findings — pilot baseline (run `pilot-1`, n=15)

Answer model `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`, Ragas 0.4.3,
`TOP_K=4`, `MIN_RELEVANCE=0.3`. Nothing in the Phase 1 pipeline was changed.

**Read this as a smoke test of the evaluation, not as a measurement of the
system.** All 15 rows are scored; in-scope metrics rest on 11 rows, and
faithfulness on 10. Per-chapter means rest on one to three rows each and are
not discussed below.

## 1. The seven-principles question fails completely, and retrieval is why

`q007` ("walk me through the seven testing principles") is the only in-scope row
the bot declined: status `no_context`, **context precision 0.000, context recall
0.000**, and it is the worst row in the run (`worst_10` average 0.0). The
in-scope answer rate of 90.9% (10/11) is this one row.

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

## 3. Multi-chunk rows retrieve far worse — on five rows

Splitting the eleven scored in-scope rows by `multi_chunk`:

| | n | context precision | context recall |
|---|---|---|---|
| single-chunk | 6 | 0.940 | 0.944 |
| multi-chunk | 5 | 0.567 | 0.578 |

That is a large gap in the direction the dataset was designed to expose, and it
is consistent with finding 1. The two rows added by the resume both landed in
the multi-chunk group and both scored badly on retrieval — `q058` got context
precision 0.500 and **context recall 0.000**, and `q048` is the one row whose
pages (50, 51) were retrieved perfectly. So the gap survived the sample growing
from 3 to 5, which it need not have.

Still **n=5 against n=6**, and `q007` (0.0 on both retrieval metrics) is one of
the five. Treat it as a hypothesis the full 60-row run should test, not as an
established result.

Note the same two groups invert on faithfulness (multi-chunk 0.972 vs 0.762).
That is **likely** an artifact of which rows survived to be judged — the two
missing faithfulness verdicts are both multi-chunk-adjacent — rather than a real
effect.

## 4. One K3 row scored 0.100 on faithfulness while being correct

`q027` (equivalence partitioning on an 18–65 age field) has the third-worst
average in the run (0.656), driven by **faithfulness 0.100** against context precision
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

## 5. The free tier shapes the run: a daily token cap, and a per-request ceiling

Three operational findings worth recording.

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

**A single request can be too big to ever succeed.** Resuming failed with
`Request too large ... OTPM Limit 1000, Requested 1240`. Groq sizes a request
from `max_tokens`, so an uncapped judge request was rejected on arrival and no
amount of retrying could have helped. `JUDGE_MAX_TOKENS` (default 950) now caps
the judge, and that error class is recognised as non-retryable: the row is
skipped immediately rather than backed off. Measured judge output is ~231
tokens per call, so the cap is far above a normal verdict.

**Two faithfulness verdicts were lost to that cap, and they are not parse
failures.** `q045` and `q048` both returned `finish_reason: length` — the cap cut
the verdict off mid-generation. At 800 the same happened to `q058` too; at 950
`q058` recovered (0.9167) but `q048` truncates repeatably, since the judge runs
at temperature 0. These are now counted as `nan_truncated`, separately from
`nan_parse_failure`, and only the latter feeds the validity guard. The run has
**zero** unexplained parse failures. Conflating the two would have declared a
run invalid for a cause we had already diagnosed and named.

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
5. **Recover the truncated faithfulness verdicts.** `q045` and `q048` have no
   faithfulness score because 950 output tokens is not enough for the judge to
   finish, and the free tier's 1000-per-minute ceiling leaves no headroom to
   raise it. A paid tier, or a faithfulness prompt that emits a shorter verdict,
   would close the last gap in the pilot.

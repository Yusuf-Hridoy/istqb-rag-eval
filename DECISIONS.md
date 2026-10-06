# DECISIONS.md

Ambiguities in the Phase 1 brief resolved with the simplest option, as instructed.

## Answer model: openai/gpt-oss-120b (brief says llama-3.3-70b-versatile)

The brief pins `llama-3.3-70b-versatile`, but Groq no longer serves it (as of
2026-10-05 the catalog has no Llama models at all; querying it returns
"model_not_found"). Switched to `openai/gpt-oss-120b`, the strongest general
chat model in the current Groq catalog. Everything else about the LLM call is
unchanged (temperature 0, max_retries 3, single call).

## Repo location

The brief says the repo is "cloned locally" but no clone existed. The project
was created at `~/Ragas-Test/istqb-rag-eval` (inside the working directory that
held the PDF and `.env`), initialised with `git init -b main`, and pushed to a
new GitHub repo at the end of the phase.

## Content page range: 14–63

Inspecting `ISTQB_CTFL_Syllabus_v4.0.1.pdf` (78 pages, viewer page numbers):

- Pages 1–13 are cover, copyright, table of contents and the introduction
  (chapter 0). Page 14 starts "1. Fundamentals of Testing".
- **Page 64 starts "Appendix A – Learning Objectives/Cognitive Level of
  Knowledge"** (Phase 1.1 patch: the range used to run to 75, but the appendix
  LO tables added retrieval noise without answer content).
- Pages 76–78 are the index.

So `FIRST_CONTENT_PAGE=14`, `LAST_CONTENT_PAGE=63` (defaults in `config.py`,
overridable via `.env`). Re-ingested at 1.1: 50 content pages, 167 chunks.

## Defaults in config.py

`FIRST_CONTENT_PAGE`/`LAST_CONTENT_PAGE` have code defaults (14/75) rather than
empty, so a fresh clone without a tailored `.env` ingests the right range for
this exact PDF. Everything else defaults as in `.env.example`.

## Header/footer removal

Any non-empty line occurring on more than half of the kept pages is dropped.
Lines are compared after stripping and after collapsing digit runs to `#`, so
per-page footers like "Page 14 of 78" (identical except for the page number)
correctly count as one repeated line. For this PDF that removes "Certified
Tester", "Foundation Level", "v4.0.1", "Page N of 78", "2024-09-15" and the
copyright line. This is still the brief's exact rule — nothing fancier — with
digit normalization so the page-number footer actually qualifies.

## Smoke test 1 (seven testing principles): known retrieval miss, kept honest

Q1 ("What are the seven testing principles?") returns `no_context` and is the
one red row in `docs/smoke-test-phase-1.md`. This is a genuine limitation of
the pinned stack, not a pipeline bug, and it was kept honest rather than tuned
away:

- The principles list spans four continuation chunks (`p17-4`, `p18-1`,
  `p18-2`, `p18-3`); chunking is per page, so no single chunk can hold it.
- With the brief-pinned `BAAI/bge-small-en-v1.5`, the list chunks rank far
  outside top-k (`p18-1` at rank 76/197); the top ranks are learning-objective
  pages that name the topic ("Explain the seven testing principles") without
  containing it.
- Four stronger embedding models were measured on the same chunks
  (`bge-base-en-v1.5`, `bge-large-en-v1.5`, `snowflake-arctic-embed-l`, and
  bge-small with its query instruction prefix): none puts the needed chunks in
  the top 4. Larger chunks (2000–3000 chars) and page-level granularity rank
  *worse* (dilution).
- With the actual top-4 excerpts, the LLM's not-found reply is the *correct*
  behaviour per the grounded prompt — the excerpts genuinely do not contain
  the answer. Forcing a green check would require hallucination, huge k, or
  section-aware chunking — all explicitly out of Phase 1 scope.

This is the baseline the Ragas evaluation in Phase 2 will quantify (context
recall on list questions) and Phase 3's section-aware chunking experiment is
expected to fix. Fixing it now would destroy the eval story and violate scope.

## Relevance scores

`similarity_search_with_relevance_scores` returns 0–1 (higher = closer) only
when the Chroma collection uses cosine distance, so ingestion creates the
collection with `hnsw:space: cosine` and `MIN_RELEVANCE=0.3` is interpreted on
that scale.

## Chunk IDs

`p<page>-<n>` where `<page>` is the 1-based printed page number and `<n>` is
the chunk's position within that page (e.g. `p42-1`, `p42-2`).

## Refused / no_context handling in the UI

Both fixed replies render with Streamlit's `st.info` (not `st.error`), per the
brief; only pipeline failures (status `error`) use `st.error`.

## Streamlit "store ready" check

The app checks that `.chroma/` exists and is non-empty before offering the chat;
otherwise it shows the ingest command. It does not validate the collection
schema — the simplest proxy that avoids a broken chat UI.

## Judge model: qwen/qwen3.8-27b on Groq (Phase 2)

The brief called for Gemini as the judge, a different family from the
gpt-oss-120b answer model. That ran into a hard wall, so the judge moved to
Groq. The history, all of it measured rather than assumed:

1. `gemini-3.8-flash` was the first choice. It is a real model and answers
   normally, but the 429 payload reports
   `quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue=20`
   — **20 requests per day, per model**, on this account's free tier.
2. `gemini-3.5-flash` was tried next and reported exactly the same 20/day cap.
   `gemini-flash-latest` is an alias for 3.8-flash and shares its quota.
3. A measured row needs **8 judge calls** (see `measure` below), so a 60-row
   baseline needs ~480 calls. At 20/day that is roughly 24 days per run, which
   makes the judge-variance work in Phase 3 impossible.
4. The exact free-tier RPD for `gemini-3.1-flash-lite` could not be confirmed:
   the rate-limit docs page defers to AI Studio, which needs an interactive
   login, and probing it would have burned the quota being measured. Every
   Gemini model whose cap *was* observed on this account reported 20/day, and
   the quota id is per-project-per-model, so there is no reason to expect
   3.1-flash-lite to differ. Treated as < 400/day.

So the judge is **`qwen/qwen3.8-27b` on Groq**, still a different model family
from `openai/gpt-oss-120b`. Note the brief named `qwen/qwen3.6-27b`, which does
not exist on Groq; `qwen/qwen3.8-27b` is the Qwen model actually served.

Measured Groq limits for it (from `x-ratelimit-*` response headers):

| Limit | Value |
|---|---|
| Requests | 1000/day (reported 991 remaining) |
| Tokens | 8000/minute |

The request budget fits a 480-call baseline comfortably. The **token** budget is
the binding constraint: one row costs ~9.1k prompt + ~1.9k completion tokens,
so a single row exceeds the per-minute token bucket. Expect the baseline to be
paced by TPM (roughly 80+ minutes for 60 rows) and to hit transient 429s. That
is what the stop-and-resume behaviour below is for.

`JUDGE_MODEL` still selects the provider: a `gemini*` name routes to
`ChatGoogleGenerativeAI`, anything else is treated as a Groq model id.

## Ragas 0.4.3: three API differences from the brief

All three were found by running the scorer, and each one silently produced NaN
rather than an error, so they are worth recording.

1. **Metric column names.** `evaluate(...).to_pandas()` names each column after
   the metric object, not our key. `response_relevancy` is Ragas'
   `answer_relevancy`, so looking the column up by our own key raised
   `KeyError`. We now look up by `metric.name`.
2. **`answer_relevancy` asks for multiple candidates.** It calls the judge with
   `n=self.strictness` (default 3). The Gemini API rejects that with
   `400 INVALID_ARGUMENT: Multiple candidates is not enabled for this model`.
   We set `answer_relevancy.strictness = 1`. Kept after the move to Groq so the
   judge stays swappable.
3. **FastEmbed breaks Ragas' telemetry.** `LangchainEmbeddingsWrapper` builds an
   `EmbeddingUsageEvent` with `getattr(embeddings, "model", None)` and pydantic
   requires a string, but `FastEmbedEmbeddings.model` is a `TextEmbedding`
   object. The ValidationError turned every embedding-backed metric into NaN.
   `_FastEmbedForRagas` in `eval/step2_judge_scores.py` wraps it and exposes `.model` as the
   model-name string.

Ragas 0.4.3 also warns that importing from `ragas.metrics` is deprecated and
will be removed in v1.0, in favour of `ragas.metrics.collections`. We stay on
`ragas.metrics` for Phase 2 because the version is pinned in `uv.lock` and the
brief asks for a stable baseline; migrating is a Phase 3 chore.

## Quota handling and the two kinds of NaN

A NaN from Ragas means two very different things, and conflating them produced
a run that looked finished but measured nothing:

* **API error** — the judge never answered. The row was not measured, so it is
  *not* written to scores.csv and a rerun retries it.
* **Parse failure** — the judge answered and Ragas could not read it. That is a
  real outcome, so it is written as an empty cell and counted.

`JudgeCallCounter` (a LangChain callback) tells them apart by watching
`on_llm_error`, which is also where the per-row call and token counts come
from.

### Not every 429 is exhaustion

Stopping on *any* 429 was the first implementation, and it made a baseline
impossible: the very first real run died on

    ... on output tokens per minute (OTPM): Limit 1000, Used 338,
    Requested 674. Please try again in 720ms.

That is a **720-millisecond** token-bucket wait, not a daily cap. So
`is_daily_quota_error()` now stops only for limits this run cannot wait out —
wording that names a daily cap, or a requested wait over
`DAILY_WAIT_THRESHOLD_S` (300s). Gemini's `retryDelay: 45700s` stops the run;
Groq's 720ms sleeps for the time the provider asked for and carries on, up to
`MAX_ROW_ATTEMPTS`. When the stage does stop it prints how many rows are left
and leaves them unscored, so the same command resumes.

A bug worth remembering from that parser: the regex captured `720ms.` including
the sentence's full stop, which failed the millisecond branch and fell through
to the h/m/s branch, reading `720m` as **12 hours** and tripping the daily
path. The match is now stripped of trailing dots and milliseconds are matched
with `fullmatch`. `tests/test_eval.py` pins all four wordings.

### max_workers=1

The brief specified `RunConfig(max_workers=2)` to stay inside free-tier limits.
With the Groq judge that is counterproductive: one row costs ~11k tokens
against an 8000 TPM / 1000 OTPM bucket, so a single row already exceeds the
per-minute budget and a second worker only manufactures 429s. Set to 1. The
baseline is therefore paced by the token bucket — expect well over an hour for
60 rows — rather than by request count.

`run_report` refuses to write summary.json when any metric's parse-failure rate
is above 10%, so a judge that mostly failed can never be mistaken for a
baseline. NaN counts are now taken only over rows the routing table says should
have been judged — previously an out-of-scope row counted as four NaNs.

## Phase 3 candidates

* **Lower TOP_K to cut judge cost.** Context precision asks the judge once per
  retrieved context, so at `TOP_K=4` it is half of the 8 calls a row costs.
  Dropping to `TOP_K=3` would cut the baseline by roughly 60 calls and a
  proportional slice of the token bill. Not touched in Phase 2, which measures
  the Phase 1 system exactly as it is.
* Migrate off the deprecated `ragas.metrics` imports.
* Re-check whether a paid Gemini tier makes the original cross-provider judge
  (Gemini judging Groq) affordable, which would be a cleaner independence story
  than Qwen judging gpt-oss.

## Golden dataset assembly

`data/golden_dataset.jsonl` holds all 75 rows, ids `q001`–`q075`, ordered in_scope
(by chapter, then section) then not_in_syllabus then out_of_scope. The earlier
per-chapter drafts under `data/processed/` were deleted: that directory is
gitignored, so a dataset left there would never have been committed.

Every row is `source: "llm"` and `reviewed: false`. Only the user flips
`reviewed` to true, because the README's "LLM-drafted, human-reviewed" claim
has to be literally true. `MIN_REVIEWED_ROWS = 60` means the baseline refuses
to start until that review has happened.

`check_mix()` in `eval/dataset.py` fails the load unless the type counts
(60/5/10) and the per-chapter in-scope counts (10/8/6/18/14/4) match the brief
exactly, so the dataset cannot drift without someone noticing. Tests pass
`validate_mix=False` when they are exercising row-level validation on a
deliberately small file. Achieved K-levels are K1 20, K2 28, K3 12 — exactly
the brief's target — with 21 rows marked `multi_chunk`.

Both drafting rules were checked mechanically over all 75 rows: no question
shares an 8-word run with the syllabus text, and no reference exceeds 60 words.

## Eval dashboard and the fixture run

The dashboard reads only committed artifacts — `config.json`, `summary.json`,
`scores.csv` — so it works on a fresh clone with no API keys and no
`answers.jsonl`. Question text for the Worst 10 table is joined from
`data/golden_dataset.jsonl` by id, because `scores.csv` deliberately carries no text.

`runs/example-fake-data/` exists so the dashboard has something to render before any real
run. Its numbers are invented, its `config.json` sets `"fixture": true`, and
the dashboard shows a warning banner on any run carrying that flag. It must
never be read as a measurement.

The chat tab's "Score this answer" button calls the same `make_scorer()` from
`eval/step2_judge_scores.py` rather than re-creating the Ragas setup. With no reference
answer it can only compute faithfulness and response relevancy, which the
caption says; the button is hidden when no judge API key is configured.

## Pilot baseline: n=15 by user choice

The full 75-row dataset stays in `data/golden_dataset.jsonl`; the first baseline runs on
a 15-row subset marked `"pilot": true`. The remaining 60 rows are kept for later
runs, so this is a staging decision, not a reduction of the dataset.

`MIN_REVIEWED_ROWS` drops from 60 to 15 to match. The pilot subset holds its
own miniature of the brief's mix:

| | rows |
|---|---|
| in_scope | 11 — ch1 2, ch2 2, ch3 1, ch4 3, ch5 2, ch6 1 |
| K-levels | K1 4, K2 5, K3 2 |
| multi_chunk | 5 (including the seven-testing-principles row, q007) |
| not_in_syllabus | 2 |
| out_of_scope | 2, one of them a prompt injection (q072) |

**What n=15 costs.** At roughly 8 judge calls per answered row the pilot is
about 120 calls instead of ~480, which is the point. The price is that most
per-group means stop being readable: with 11 in-scope rows spread over six
chapters, four chapters land on n ≤ 2. Treat the pilot as a check that the
machinery works end to end and that the overall numbers are plausible — not as
evidence about any particular chapter or K-level.

So `_grouped()` now records `n` and `n_too_small` (below `MIN_GROUP_N = 3`) for
every group in summary.json. The console report prints `n=` beside each group
and appends "(n too small)"; the dashboard greys those cells to "n too small"
and leaves the thin groups out of the bar charts, naming them underneath
instead. Thin groups keep their means — they are marked, not dropped, because
hiding them would be its own kind of dishonesty.

`runs/example-fake-data/` was rebuilt pilot-shaped (15 rows) so the dashboard demonstrates
this path on a fresh clone. Its numbers remain invented.

## check_mix counts only LLM-drafted rows

The brief asks the user to add 3–5 of their own tricky questions as
`source: "human"`. Those would have broken the 60/5/10 and per-chapter checks,
so `dataset_counts()` now counts only `source == "llm"` rows. Human rows are
still validated row by row — schema, required fields, unique ids — they simply
do not have to fit the drafted mix.

## Pilot run `pilot-1`: what the quota actually allowed

Measured during the run, not estimated: Groq's free tier caps
`qwen/qwen3.8-27b` at **200,000 tokens per day** (`TPD`), separate from the
8000 TPM / 1000 OTPM per-minute buckets. Nine judged rows consumed essentially
all of it, so `q048` and `q058` are unscored pending a rerun. That is ~22k
tokens per judged row against the ~11k the single-row `measure` predicted. A
full 60-row baseline needs roughly 1.3M tokens and cannot run on the free tier
in one day.

The daily cap recovers on a slow rolling window — about 250 tokens per 20
minutes was observed — so waiting it out inside one session is not viable.

Two behaviours were added in response, both in the eval runner, neither touching
the Phase 1 pipeline or the judge:

* **A quota stop no longer blocks rows that need no judge.** Out-of-scope and
  not-in-syllabus rows are decided by the routing table alone. Previously the
  stage raised on the first daily-quota error and those rows were never written,
  which made scope handling unmeasurable on any day the budget ran out. The
  stage now skips rows that need the judge, still scores the judge-free ones,
  and raises at the end. This is what let the pilot reach 13 of 15 rows.
* **The NaN guard needs a count as well as a rate.** One unparseable
  faithfulness verdict out of 8 judged rows is 12.5%, over the 10% limit, so the
  guard declared the run invalid and refused to write summary.json. A single
  failure is noise. `failed_nan_metrics` now requires the rate to be exceeded
  *and* at least `MIN_NAN_FAILURES = 2` verdicts to have failed. The 10%
  threshold itself is unchanged.

## Dataset review: LLM-verified, not human-verified

The 15 pilot rows were checked page by page against the syllabus and marked
`"reviewed": true` with `"reviewed_by": "llm"` — see `docs/dataset-review.md`
for the per-row log. Six rows were corrected: three `section` fields pointed at
a parent section rather than the subsection holding the answer, `q007`'s
reference did not name all seven principles, `q045` reused the syllabus's own
worked example (changed to fresh numbers so it is a real application), and
`q072`'s injection used the stock "ignore all previous instructions" opener and
was rewritten as plausible social engineering.

`reviewed_by` is an optional field — absent on unreviewed rows — but a row with
`"reviewed": true` must name a reviewer, so the provenance of a reviewed row can
never be silent. The README states the dataset is LLM-verified and that
human-checked rows would carry `reviewed_by: human`; no row carries that yet.

## File names say what the file does

A readability pass, no behaviour change. Renamed with `git mv` so history
follows:

| was | is | why |
|---|---|---|
| `src/istqb_rag/models.py` | `result_types.py` | "models" reads as ML models here, which is exactly wrong — the file holds result dataclasses. |
| `eval/generate.py` | `eval/step1_ask_questions.py` | The three eval stages now say their order and their job. |
| `eval/score.py` | `eval/step2_judge_scores.py` | |
| `eval/report.py` | `eval/step3_build_summary.py` | |
| `data/golden.jsonl` | `data/golden_dataset.jsonl` | "golden" alone does not say it is the dataset. |
| `runs/fixture/` | `runs/example-fake-data/` | "fixture" is jargon; the new name says the numbers are invented. |

The CLI subcommands are deliberately unchanged — `generate`, `score`, `report`,
`all`, `measure` — so documented commands and the `runs/` layout keep working.
Internal aliases in `eval/__main__.py` still read `generate_mod` / `score_mod` /
`report_mod` to match those subcommand names.

`conftest.py` and `__main__.py` keep their names because pytest and Python
require them; the README's project map notes this so the inconsistency does not
look accidental.

Resume was verified after the rename: `runs/pilot-1/` still reports 13 rows
already scored and would judge only `q048` and `q058`, and the generate stage
re-asks nothing.

## JUDGE_MAX_TOKENS: why the judge needs an output cap

Resuming `pilot-1` for `q048` and `q058` failed with a Groq 429 that no amount
of retrying could clear:

    Request too large ... on output tokens per minute (OTPM):
    Limit 1000, Requested 1240. The request's expected output tokens exceed
    the enforced limit; reduce max_tokens ...

Groq sizes a request from **`max_tokens`**, not from what the reply actually
uses. With `max_tokens` unset, langchain-groq sends the model's full default
ceiling, so a single request was "1240 expected output tokens" against a
1000-per-minute budget and was rejected on arrival. Waiting changes nothing —
only a smaller cap does.

Measured judge output on the resumed rows: **3700 completion tokens over 16
calls, ~231 tokens per call**, so a cap of a few hundred is far above what a
verdict normally needs. Capping does not change a verdict that fits under the
cap: the judge is at temperature 0 and `max_tokens` only truncates, it does not
steer. It is a ceiling, not a budget the model spends up to.

`JUDGE_MAX_TOKENS` defaults to **950**. 800 was tried first and truncated the
faithfulness verdict on both resumed rows (`LLMDidNotFinishException: the LLM
generation was not completed`), which Ragas turns into NaN. At 950 `q058`
scored normally (faithfulness 0.9167) but `q048` still truncates — repeatably,
since the judge is deterministic. 950 is the agreed ceiling, so `q048`'s
faithfulness stays unmeasured rather than being bought by a larger cap that the
per-minute limit would reject anyway.

The setting applies to the judge only. The answer model is untouched, so the
13 rows scored before this change remain comparable with the 2 resumed ones on
every metric except `q048`'s faithfulness.

### "Request too large" is not a rate limit

It arrives as a 429 and matches the quota markers, but it is a per-request
ceiling: the identical request fails every time. `is_request_too_large()` now
catches it before the backoff path, so the row is skipped immediately with
"judge request exceeds provider per-request limit — lower JUDGE_MAX_TOKENS",
is not saved, and the run continues. It is also excluded from
`is_daily_quota_error()`, so it can never stop the whole stage.

The per-row retry policy moved to a module-level `score_with_retries()` driven
by callables, so this behaviour is testable without a judge or a network.

### Truncated verdicts are counted apart from parse failures

A NaN now has three possible causes, and conflating them cost a valid run once:

| cause | saved? | counts towards the guard? |
|---|---|---|
| API error — the judge never answered | no, retried on rerun | n/a |
| **Truncated** — `finish_reason: length`, our cap cut the verdict short | yes, as `nan_truncated` | **no** |
| Parse failure — the judge answered, Ragas could not read it | yes, as `nan_parse_failure` | yes |

Truncation is detected from the response's `finish_reason`, not inferred from
the NaN, so the classification is measured rather than guessed. The guard exists
to catch a judge producing unreadable output; a verdict *we* cut off with
`JUDGE_MAX_TOKENS` is a diagnosed configuration artifact with a named fix, and
reporting it as a judge failure would be wrong.

This changed the pilot's reading. `q045`'s NaN had been recorded as a parse
failure; re-scoring with the detector in place showed it was truncation all
along, the same cause as `q048`. The run therefore has **zero** unexplained
parse failures, and `summary.json` reports `nan_truncated: 2` on faithfulness so
the gap is visible rather than silently averaged away.

`scores.csv` gained a `judge_truncated` column, appended last so Phase 3's join
on `id` is unaffected. Rows written before the column existed read as not
truncated, which is correct for them.

## Phase 3: heading detection needed more than the brief's regex

The brief specifies headings as `^\d+(\.\d+){1,2}\s+[A-Z]` and warns that fewer
than 40 sections means detection failed. On this PDF that regex finds **21** —
below the brief's own failure threshold — because only top-level headings put
the number and title on one line:

    "1.1  What is Testing?"       <- matches
    "1.1.1. " / "Test Objectives" <- number and title on separate lines

So `find_headings()` matches both shapes. Two further problems surfaced while
building it, both found by checking the store rather than trusting the count:

* **The header/footer stripper was eating the numbering.** `remove_repeated_lines`
  normalises digit runs before counting, so every bare subsection number
  ("5.1.1.", "6.2.1.") collapses to the same `#.#.#.` string, appears on most
  pages, and was removed as boilerplate — taking the subsection headings with
  it. Section mode now passes a `protect` predicate that exempts bare section
  numbers. Page mode passes none and is therefore byte-identical to Phase 2,
  which a test pins.
* **Chapter contents pages duplicate the numbers.** "1.3" appears both on the
  chapter's contents page and at the real section, which produced duplicate
  `chunk_id`s and a bogus section whose body was a list of titles. For each
  section id the occurrence with the most text under it is kept.

Result: 79 distinct sections, 178 chunks, no duplicate ids, and section 1.3
holds all seven testing principles in one place — which is the q007 case
Experiment 1 exists to test.

## Phase 3: switches and where they write

`CHUNKING=page|section` and `ANSWER_FORMAT=text|structured`, both defaulting to
the Phase 2 behaviour. Section chunking writes to its own collection
(`ctfl_v4_section`, via `active_collection()`), so the baseline collection and
`runs/pilot-1` are never touched and the pilot stays reproducible.

Structured mode asks the answer model for JSON and takes the status and
citations the model states, instead of inferring both from prose. Unparseable
JSON falls back to Phase 2's text matching and sets `format_fallback` on the
result, which is counted and reported rather than hidden.

`scores.csv` gained `retrieved_pages` and `format_fallback`, appended last so
the join on `id` is unaffected. `retrieved_pages` holds page numbers only, never
text, which is what lets page hit rate be computed from the committed file.

## The answer model is not reproducible at temperature=0

Phase 3 added a control run (`runs/pilot-1-repeat`) that re-ran the pilot-1
configuration with nothing changed. It was meant to check whether Experiment 2's
gains were real. It showed something larger:

* **Retrieval is deterministic** — identical chunk ids, in identical order, on
  15 of 15 rows.
* **Generation is not** — 11 of 15 answers differed, despite
  `ChatGroq(temperature=0)` in `pipeline._build_llm`.

So `temperature=0` does not make this hosted model reproducible. The practical
rule that follows, and that the findings now apply:

| metric kind | comparable across single runs? |
|---|---|
| retrieval-side (context recall, page hit rate, retrieved pages) | yes — retrieval repeats exactly |
| answer-side (citation rate, scope accuracy, status, faithfulness) | **no** — unknown run-to-run noise |

Experiment 1's conclusion survives on that split, because context recall and
page hit rate depend on the retrieved chunks and the fixed reference pages, not
on the generated answer. Experiment 2's measured gains do not: a plain repeat of
the baseline reproduced both of them.

This also means pilot-1's own answer-side baseline numbers — citation rate
0.800, out-of-scope accuracy 0.500 — are one sample, not a fixed property of the
system. "Repeat runs to measure answer variance" is the first Phase 4 candidate
for that reason.

The judge is a separate question. It also runs at temperature 0, and Phase 2's
repeated identical truncation of `q048` is consistent with repeatability, but
that was never measured either and should not be inferred from this.

## Phase 4: ANSWER_FORMAT now defaults to structured

The variance study (`docs/answer-variance.md`) ran four samples of each mode and
applied a decision rule written before any of them. All three conditions were
met — 0 format fallbacks across 4 structured runs, no scope row ever recorded as
`answered`, and mean citation rate 1.000 against text's 0.925 — so the default
changed from `text` to `structured`.

**`runs/pilot-1` and every other text-mode run remain reproducible** by setting
`ANSWER_FORMAT=text`. The switch still exists and nothing about the text path
changed; only the default moved. The text-mode runs in the study are `pilot-1`,
`pilot-1-repeat`, `text-repeat-3` and `text-repeat-4`.

### What the study measured, and what it did not

The honest reading is narrower than "structured answers are better":

* **Structured mode stabilises the bookkeeping, not the prose.** Across 4 runs
  its citation rate and both scope accuracies never moved at all, while text
  mode's citation rate ranged 0.800–1.000 and out-of-scope accuracy 0.500–1.000.
* **Neither mode produces stable text.** Only 4 of 15 rows in text mode and 3 of
  15 in structured mode gave an identical answer across all four runs. The
  format does not make the model deterministic; it makes the *recorded status
  and citations* deterministic, because the model states them instead of having
  them inferred from whatever wording it chose.
* **`q072` is the whole of text mode's scope instability.** It flipped between
  `answered` and `refused` across the four text runs. Reading the replies
  locally, the two `answered` cases were genuine refusals in the model's own
  words that simply did not match `REFUSAL_TEXT` verbatim. Structured mode had
  no flips.
* **Answer rate never moved** in either mode (0.909, n=11, all eight runs), so
  the study says nothing about whether structured mode answers more questions.

Faithfulness was not re-measured in any of these runs — Phase 4 made zero judge
calls — so nothing here speaks to answer quality.

### A note on the run count

The brief named five new runs (`text-repeat-3`, `text-repeat-4`,
`structured-repeat-2/3/4`) while its acceptance list said six and estimated ~90
answer-model calls. Five new runs at 15 rows is 75 calls. The five named runs
were made, giving the 4 + 4 samples the study design calls for.

## Phase 4: what CI can and cannot guarantee

GitHub Actions has no syllabus PDF and no API keys, so it cannot build the index,
call a model, or reproduce any score. The gate therefore protects **code quality
and the honesty of the published results**, not the numbers themselves:

| check | what it prevents |
|---|---|
| `ruff check` / `ruff format --check` | style drift |
| `pytest -q` | 135 offline tests — no network, no keys, no PDF |
| README tables regenerate identically | a number typed by hand into the README |
| every `runs/*/` has config, scores and summary | a half-written run being cited |
| `scores.csv` columns are allowlisted | a column that could carry question, answer or syllabus text |
| golden dataset validates, reviewed rows name a reviewer | silent provenance loss |

The README-table check is the load-bearing one. Each table lives between
`<!-- results:<name>:start -->` markers, `eval readme-tables` regenerates them
from the run files, and the check rebuilds them in memory and fails if the file
differs. Editing `0.778` to `0.999` in the README now fails the build; there is
a test that asserts exactly that.

The allowlist is taken from `SCORES_COLUMNS`, so adding a column to the scorer
automatically permits it — the check guards against *unknown* columns appearing
in a committed file, not against the schema evolving deliberately.

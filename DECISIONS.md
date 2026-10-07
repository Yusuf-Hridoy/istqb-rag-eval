# Decisions

One entry per decision. Results and evidence live in [docs/findings.md](docs/findings.md).

## Pipeline

**Answer model `openai/gpt-oss-120b` on Groq.**
Fast and free-tier friendly; the brief's suggested model was unavailable.

**Content pages 14–63 only.**
Page 14 starts Chapter 1 and page 63 is the last content page before Appendix A.
Appendices are glossary and exam structure, not syllabus content.

**Repeated header/footer lines are stripped before chunking.**
The same banner and footer on every page otherwise dominate short chunks.

**`store._collection.count()` runs inside a `try`.**
A broken store should return `status="error"`, not raise out of `answer()`.

## Evaluation

**Judge is `qwen/qwen3.8-27b` on Groq, a different family from the answer model.**
Gemini was the first choice but its free tier allows **20 requests/day/model**,
measured from the 429 payload. A row costs 8 judge calls, so a 60-row baseline
would take ~24 days.

**`JUDGE_MAX_TOKENS=950`.**
Groq sizes a request from `max_tokens`, so an uncapped judge request is rejected
outright against a 1000-output-tokens-per-minute ceiling. Measured judge output
is ~231 tokens/call, so 950 is ample. 800 truncated real verdicts.

**"Request too large" is never retried.**
It arrives as a 429 but is a per-request ceiling: the identical request fails
every time. Only a smaller `JUDGE_MAX_TOKENS` fixes it.

**A per-minute rate limit is retried; a daily cap stops the stage.**
Treating every 429 as exhaustion killed the first real run after 0 rows, on a
720-millisecond token-bucket wait. Daily caps are detected by wording or by a
requested wait over 5 minutes.

**A quota stop still scores rows that need no judge.**
Out-of-scope and not-in-syllabus rows are decided by the routing table, so judge
quota cannot affect them. Otherwise scope accuracy is unmeasurable on any day
the budget runs out.

**Three kinds of NaN are counted apart: API error, truncated, parse failure.**
An API error means the row was not measured, so it is not saved and a rerun
retries it; a verdict truncated by our own cap is a diagnosed artifact. Only an
unexplained unreadable verdict counts towards validity. Truncation is read from
`finish_reason`, not inferred.

**A run is invalid only if a metric's parse-failure rate exceeds 10% *and* at
least 2 verdicts failed.**
A rate alone is meaningless on a small run: at 8 judged rows a single failure is
12.5%.

**Group means carry `n`, and groups under 3 rows are marked "n too small".**
At n=15 most per-chapter means rest on one or two rows.

**Metrics a run never requested are reported as `unmeasured`, not as failures.**
A column of blanks is otherwise indistinguishable from a judge that failed on
every row.

**Answers are generated once and scored separately.**
Re-scoring then costs no answer-model calls.

## Dataset

**75 rows, LLM-drafted; the first baseline uses the 15 marked `pilot: true`.**
A 60-row judged baseline exceeds the free-tier daily token budget. The remaining
rows are kept for later runs.

**Reviewed rows must name a reviewer (`reviewed_by`).**
Provenance of a reviewed row can never go silent. The 15 pilot rows carry
`reviewed_by: llm`; no row is human-verified.

**`check_mix()` counts only `source: "llm"` rows.**
Lets the user add their own questions without breaking the 60/5/10 mix.

## Experiments

**Every experiment changes one variable, behind a switch whose default was the
previous behaviour.**
Keeps the baseline reproducible while the experiment runs.

**Section chunking writes to its own collection (`ctfl_v4_section`).**
The baseline collection and `runs/pilot-1` are never touched.

**Heading detection matches two shapes, not the one the brief specified.**
Only top-level headings put number and title on one line; the specified regex
found 21 sections, below the brief's own 40-section failure threshold. Matching
both shapes finds 79.

**Section mode exempts bare section numbers from header/footer stripping.**
The stripper normalises digits, so every `5.1.1.` collapsed to `#.#.#.`, looked
like boilerplate on most pages, and was removed — taking the headings with it.
Page mode passes no exemption and stays byte-identical.

**For a duplicated section number, the occurrence with the most text wins.**
Chapter contents pages list the same numbers as the body.

**Section chunking was not adopted.**
It made retrieval worse and did not fix the row it was built for.

**`ANSWER_FORMAT` defaults to `structured`.**
A decision rule written before the runs; all three conditions met. It stabilises
the recorded status and citations, not the prose — neither mode produces stable
text. `ANSWER_FORMAT=text` reproduces every text-mode run.

**Experiment 2's measured gains were not credited.**
A repeat of the unchanged baseline reproduced both of them.

**Answer prompt is version 2: 2-4 sentences, direct answer first, syllabus
wording only.**
The 150-word rule allowed long answers that restated the excerpts.
`PROMPT_VERSION` is recorded in every new run's config.json. **Every run in
`runs/` and every number in the README was produced with version 1.** A
judge-free check (`prompt-v2-check`) showed no change in citation rate,
citation validity, page hit rate, scope accuracy or answer rate, and no row
changed status; answer length moved only slightly (median 55 to 50 words) and
not in one direction (the longest answer grew), which on one run of a
nondeterministic model is not attributable.

## Interface

**The chat tab renders an answer once, from `_render_result`.**
The history loop also printed `message["content"]`, which for an assistant row
is the same string, so every past answer appeared twice.

**"Sources used" lists only chunks the answer cited; the rest are collapsed
separately.**
Retrieval always returns `TOP_K` chunks, so listing all of them implied the
answer used them all. Retrieval itself is unchanged.

**Auto-scoring is off by default.**
Each score costs about 3 judge calls, so it is opt-in per session.

## Honesty guards

**Status is matched by string equality, and that is a known flaw.**
A refusal in the model's own words is recorded as `answered`. Changing the
pipeline was out of scope while the eval measured it; structured mode removes
the failure class instead.

**Citation validity is reported next to citation rate.**
Letting the model state its own pages could raise the rate by inventing them. A
row whose retrieved pages were never recorded is *unvalidatable*, not invalid.

**Committed files never contain syllabus, question or answer text.**
`answers.jsonl` is gitignored. `scores.csv` holds
ids, statuses, scores, page numbers and an answer hash.

**No published number may come from a gitignored file.**
Two paths read `answers.jsonl` to build README tables, which worked locally and
failed on a clean checkout. `scores.csv` now carries `retrieved_pages`,
`format_fallback` and `answer_sha256` natively.

**A missing value in a published table fails the build.**
Rendering `n/a` instead of crashing is only safe if something refuses to ship it.

**README results tables are generated, never typed.**
CI regenerates them in memory and fails on any difference.

**CI cannot verify scores, and says so.**
It has no syllabus PDF and no API keys. It guards code quality and the honesty
of published results.

## Library notes

**Ragas pinned at 0.4.3**, with three differences from its docs, each of which
silently produced NaN: the dataframe column is named after the metric object
(`answer_relevancy`, not `response_relevancy`); `answer_relevancy` requests
`n=strictness` candidates, which some providers reject, so strictness is 1; and
its telemetry needs a string `.model` on the embeddings object, which FastEmbed
lacks — a thin wrapper supplies one.

**Tried Ragas noise sensitivity, then dropped it.**
Measured on one question: ~11 judge calls, ~17k tokens and ~5 minutes. Too
costly for a free-tier judge budget of 200k tokens a day, so the metric was
removed rather than left half-run. No score is reported — one question is not a
result.

**`max_workers=1` for the judge.**
One row's ~11k tokens already exceeds the per-minute budget, so parallel rows
only cause 429s.

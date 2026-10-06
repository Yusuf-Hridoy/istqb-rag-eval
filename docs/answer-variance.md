# Answer variance study

Phase 3 found that `temperature=0` does not make this hosted answer model
repeat: between two identical runs, retrieval matched on 15 of 15 rows but
11 of 15 answers differed. Every answer-side number in this project is therefore
one sample from an unmeasured distribution. This study measures that spread and
asks whether structured mode still looks better once the noise is visible.

Zero judge calls. Every number here is deterministic or comes from the answer
model only.

## Decision rule

**Written before any run in this study, and applied exactly as written. If the
result is ambiguous, that is recorded as ambiguous rather than reinterpreted.**

> Make `ANSWER_FORMAT=structured` the default if, across its 4 runs:
> **(a)** total format fallbacks are at most 1,
> **(b)** no `out_of_scope` or `not_in_syllabus` row is ever recorded as
> `answered`, and
> **(c)** its mean citation rate is not lower than text mode's mean.
> Otherwise keep `text` as the default and record which condition failed.

Runs in the study:

| mode | runs |
|---|---|
| text | `pilot-1`, `pilot-1-repeat`, `text-repeat-3`, `text-repeat-4` |
| structured | `exp2-structured-answers`, `structured-repeat-2`, `structured-repeat-3`, `structured-repeat-4` |

_Results below are written by `uv run python -m istqb_rag.eval variance`._

<!-- variance:results:start -->
### 1. Spread per mode

Mean across the mode's 4 runs, with (min–max) underneath it.

| metric | text (4 runs) | structured (4 runs) |
|---|---|---|
| Citation rate | 0.925 (0.800–1.000) | 1.000 (1.000–1.000) |
| Out-of-scope accuracy | 0.750 (0.500–1.000) | 1.000 (1.000–1.000) |
| Not-in-syllabus accuracy | 1.000 (1.000–1.000) | 1.000 (1.000–1.000) |
| Answer rate | 0.909 (0.909–0.909) | 0.909 (0.909–0.909) |
| Format fallbacks (total) | 0 | 0 |

Per run, with the n behind each rate:

| run | mode | citation rate | out-of-scope acc | not-in-syllabus acc | answer rate |
|---|---|---|---|---|---|
| `pilot-1` | text | 0.800 (n=10) | 0.500 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `pilot-1-repeat` | text | 1.000 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `text-repeat-3` | text | 0.900 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `text-repeat-4` | text | 1.000 (n=10) | 0.500 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `exp2-structured-answers` | structured | 1.000 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `structured-repeat-2` | structured | 1.000 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `structured-repeat-3` | structured | 1.000 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |
| `structured-repeat-4` | structured | 1.000 (n=10) | 1.000 (n=2) | 1.000 (n=2) | 0.909 (n=11) |

### 2. Status flips

**text**: 1 row(s) changed status across its 4 runs.

| row | type | statuses seen |
|---|---|---|
| `q072` | out_of_scope | answered, refused |

**structured**: no row changed status across its 4 runs.

### 3. Answer stability

How many distinct answers a row produced across its mode's 4 runs
(compared by SHA-256; no answer text is stored).

| mode | rows giving the same answer every time | share |
|---|---|---|
| text | 4 of 15 | 0.267 |
| structured | 3 of 15 | 0.200 |

**text** — rows with more than one distinct answer: `q001` (3), `q011` (4), `q013` (4), `q020` (4), `q025` (4), `q027` (4), `q030` (3), `q045` (4), `q048` (4), `q058` (4), `q072` (2)

**structured** — rows with more than one distinct answer: `q001` (4), `q007` (3), `q011` (2), `q013` (4), `q020` (3), `q025` (4), `q027` (4), `q030` (4), `q045` (2), `q048` (4), `q058` (4), `q072` (4)

### 4. Scope rows recorded as answered

**text**: 2 case(s).

| row | type | run | reading of the reply |
|---|---|---|---|
| `q072` | out_of_scope | `pilot-1` | Read locally: a real refusal in the model's own words (an apology declining the request), not an answer. Recorded `answered` only because it does not match REFUSAL_TEXT verbatim. |
| `q072` | out_of_scope | `text-repeat-4` | Read locally: a real refusal in the model's own words (an apology declining the request), not an answer. Recorded `answered` only because it does not match REFUSAL_TEXT verbatim. |

**structured**: none.

Readings were taken locally from the run's `answers.jsonl`, which is gitignored. The replies themselves are not reproduced here.

### 5. Decision-rule outcome

| condition | met | evidence |
|---|---|---|
| (a) total format fallbacks at most 1 | yes | 0 fallback(s) across 4 runs |
| (b) no out_of_scope or not_in_syllabus row ever recorded as answered | yes | none |
| (c) mean citation rate not lower than text mode's | yes | structured 1.0 vs text 0.925 |

**All three conditions met. `ANSWER_FORMAT` default becomes `structured`.**
<!-- variance:results:end -->

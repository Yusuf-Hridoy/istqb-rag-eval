# Evaluation results

Detailed evaluation results. Generated from the run files; CI fails if they don't match.

## Baseline

<!-- results:baseline:start -->
Run `pilot-1` — 15 rows, judged by `qwen/qwen3.8-27b`.

| Metric | Mean | Rows scored (n) | NaN (parse / truncated) |
|---|---|---|---|
| Context precision | 0.770 | 11 | 0 / 0 |
| Context recall | 0.778 | 11 | 0 / 0 |
| Faithfulness | 0.841 | 8 | 0 / 2 |
| Response relevancy | 0.869 | 10 | 0 / 0 |

In-scope answer rate 0.909 (10/11) · errors 0 · latency median 8829 ms, p95 12021 ms.
<!-- results:baseline:end -->

## Experiments

<!-- results:experiments:start -->
| metric | baseline<br>`pilot-1` | control, same config<br>`pilot-1-repeat` | section chunking<br>`exp1-section-chunking` | structured answers<br>`exp2-structured-answers` |
|---|---|---|---|---|
| Context recall | 0.778 (n=11) | not re-judged | 0.667 (n=11) | not re-judged |
| Page hit rate | 1.000 (n=11) | 1.000 (n=11) | 0.818 (n=11) | 1.000 (n=11) |
| Citation rate | 0.800 (n=10) | 1.000 (n=10) | 1.000 (n=10) | 1.000 (n=10) |
| Citation validity | 1.000 (n=8) | 1.000 (n=10) | 1.000 (n=10) | 1.000 (n=10) |
| Out-of-scope accuracy | 0.500 (n=2) | 1.000 (n=2) | 1.000 (n=2) | 1.000 (n=2) |
<!-- results:experiments:end -->

## Answer variance

<!-- results:variance:start -->
Mean across 4 runs of each mode, with (min–max).

| metric | text (4 runs) | structured (4 runs) |
|---|---|---|
| Citation rate | 0.925 (0.800–1.000) | 1.000 (1.000–1.000) |
| Out-of-scope accuracy | 0.750 (0.500–1.000) | 1.000 (1.000–1.000) |
| Not-in-syllabus accuracy | 1.000 (1.000–1.000) | 1.000 (1.000–1.000) |
| Answer rate | 0.909 (0.909–0.909) | 0.909 (0.909–0.909) |
| Format fallbacks (total) | 0 | 0 |
| Rows with an identical answer every run | 4 of 15 | 3 of 15 |

Status flips across runs — text: `q072`; structured: none.

Decision rule (written before the runs): **default = `structured`**.
<!-- results:variance:end -->

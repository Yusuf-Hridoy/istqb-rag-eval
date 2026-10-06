# Failure taxonomy — pilot-1 (n=15)

A row counts as failed if any metric is below 0.7, its status is wrong for its
type, it was answered with no citation, or its judge verdict is missing. Eight
of the 15 pilot rows failed something. Each gets exactly one **primary** type —
the thing that would have to be fixed first.

| id | type | evidence | targeted by |
|---|---|---|---|
| q007 | `retrieval_miss` | Reference pages 17–18; retrieval returned 17, 14, 14, 48 — the page holding six of the seven principles was never retrieved. Context recall 0.000. | Experiment 1 |
| q058 | `retrieval_miss` | Reference pages 59–60; retrieval returned 22, 59, 16, 22. Context recall 0.000, precision 0.500. | Experiment 1 |
| q001 | `ranking_noise` | Reference page 15 **was** retrieved (48, 18, 15, 48) and recall is 0.889, but two of four chunks are page 48. Context precision 0.333. | Experiment 1 |
| q011 | `missing_citation` | Answered with `cited_pages` empty. Recall 0.667 is the secondary issue; the citation is the one a reader would notice. | Experiment 2 |
| q072 | `status_mislabel` | Replied *"I'm sorry, but I can't provide that."* — a correct refusal — but recorded `answered`, because status was matched against two fixed sentences. | Experiment 2 |
| q045 | `judge_truncated` | `judge_truncated: true`; the faithfulness verdict hit `finish_reason: length`. Retrieval was perfect (precision 1.0, recall 1.0). | neither |
| q048 | `judge_truncated` | `judge_truncated: true`, same cause. Precision 1.0, recall 1.0. | neither |
| q027 | **unassigned** | Faithfulness 0.100 on an answer that cites the correct pages (39, 40) and retrieved them. This is either `unfaithful_answer` or `judge_error`, and the two are told apart **only** by the human label. | — |

## Counts

| type | count |
|---|---|
| `retrieval_miss` | 2 |
| `ranking_noise` | 1 |
| `missing_citation` | 1 |
| `status_mislabel` | 1 |
| `judge_truncated` | 2 |
| `unfaithful_answer` | **not yet assignable** |
| `judge_error` | **not yet assignable** |
| unassigned (q027) | 1 |
| **total failed rows** | **8 of 15** |

## Why two types are still empty

`unfaithful_answer` and `judge_error` are distinguished by exactly one piece of
evidence: whether a human, reading only the retrieved chunks, agrees with the
judge. `data/human_labels.csv` was never filled in — it is byte-identical to the
file generated in Part A — so neither type can be assigned without guessing, and
guessing is precisely what the brief forbids here.

q027 is the row that hangs on this. The judge scored its faithfulness 0.100
while the answer cites the right pages and retrieval returned them. If a human
says the answer is faithful, the row is `judge_error`; if not, it is
`unfaithful_answer`. The brief is explicit that a row must never be labelled
`judge_error` on the model's own reading of the answer, so it stays unassigned
until the labels exist.

**q027 remains unassigned.** Nothing in this phase changed that: it was not
re-judged in either experiment, and the control run in §3a of the findings does
not bear on it. Note that q027's answer differs between `pilot-1` and
`pilot-1-repeat` — the answer model is not reproducible at `temperature=0` — so
a future label must be taken against a specific run's answer and chunks, not
against "the" answer.

Seven rows carry types that depend on no judgement at all — retrieved pages,
recorded status, citation presence and the truncation flag are all facts on
disk — so those are final.

## After the experiments

Section chunking (Experiment 1) did not clear either `retrieval_miss`: q007 and
q058 both still have context recall 0.000, and q001's row got worse rather than
better. These are retrieval-side numbers, and retrieval is deterministic across
runs, so the comparison holds.

Structured answers (Experiment 2) cleared both rows it targeted — q011 cites
pages 26 and 24, q072 is recorded `refused` — but **so did a plain repeat of the
unchanged baseline** (`runs/pilot-1-repeat`). Both `missing_citation` and
`status_mislabel` therefore clear without any change to the system, and the
format cannot be credited for the counts. What the format does change is that
`status_mislabel` becomes impossible by construction rather than dependent on
the model's wording. See the control run in [findings.md](findings.md).

# istqb-rag-eval

A retrieval-augmented assistant that answers questions **only** from the ISTQB
Certified Tester Foundation Level (CTFL) v4.0 syllabus — with page citations,
an off-topic refusal, and a relevance floor. It is deliberately a plain,
textbook LangChain RAG pipeline, because the point of the project is what comes
next: evaluating this exact pipeline with [Ragas](https://ragas.io/).

## How it works

- The syllabus PDF is loaded with PyMuPDF, boilerplate header/footer lines are
  stripped, and the content is split into chunks (page metadata preserved).
- Chunks are embedded with `BAAI/bge-small-en-v1.5` (FastEmbed, no PyTorch) and
  stored in a local Chroma collection (cosine distance).
- `answer(question)` is the single entry point: retrieve the top-k chunks,
  refuse to call the LLM if the best relevance score is below the floor,
  otherwise generate one grounded answer with Groq (`openai/gpt-oss-120b`,
  temperature 0) that cites pages like `[p. 42]`.
- Single-turn only: no chat history is ever passed to the LLM, so results are
  reproducible for evaluation.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

1. **Download the syllabus yourself** (it is never downloaded automatically and
   never committed): get the ISTQB CTFL v4.0 syllabus PDF from
   [istqb.org](https://www.istqb.org/certifications/certified-tester-foundation-level)
   and save it as `data/raw/ctfl_syllabus_v4.pdf`.

2. Install dependencies and create your local config:

   ```bash
   uv sync
   cp .env.example .env
   # put your Groq API key in .env (free at https://console.groq.com/keys)
   ```

3. Ingest the syllabus into the local vector store (re-running rebuilds it):

   ```bash
   uv run python -m istqb_rag.ingest
   ```

4. Ask a question from the terminal:

   ```bash
   uv run python -m istqb_rag.cli "What is equivalence partitioning?"
   uv run python -m istqb_rag.cli "What is equivalence partitioning?" --json
   ```

5. Or launch the chat UI:

   ```bash
   uv run streamlit run app/streamlit_app.py
   ```

## Tests and lint

Everything runs offline — no API keys, no PDF, no network:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Project map

| Path | What it is for |
|---|---|
| `src/istqb_rag/config.py` | Reads settings from `.env` (models, chunk size, `TOP_K`, paths). |
| `src/istqb_rag/ingest.py` | Turns the syllabus PDF into chunks and loads them into the Chroma store. |
| `src/istqb_rag/pipeline.py` | The RAG pipeline itself: `answer(question)` retrieves, generates and cites. |
| `src/istqb_rag/prompts.py` | Every prompt and fixed reply, kept in one place. |
| `src/istqb_rag/result_types.py` | The result objects passed around: `RetrievedChunk` and `RagResult`. |
| `src/istqb_rag/cli.py` | Ask one question from the terminal. |
| `src/istqb_rag/eval/dataset.py` | Loads and validates the golden dataset, and checks its mix. |
| `src/istqb_rag/eval/step1_ask_questions.py` | Eval step 1 — ask every dataset question, save the answers. |
| `src/istqb_rag/eval/step2_judge_scores.py` | Eval step 2 — the judge scores those saved answers. |
| `src/istqb_rag/eval/step3_build_summary.py` | Eval step 3 — add the scores up into `summary.json` and a console table. |
| `src/istqb_rag/eval/__main__.py` | The `generate` / `score` / `report` / `all` / `measure` commands. Keeps this name because `python -m istqb_rag.eval` requires it. |
| `app/streamlit_app.py` | The web UI: chat tab plus the eval dashboard. |
| `data/golden_dataset.jsonl` | The 75 evaluation questions with their reference answers. |
| `data/raw/`, `data/processed/` | The syllabus PDF and extracted page text. Both gitignored — syllabus text is never committed. |
| `runs/pilot-1/` | The real pilot baseline run (n=15). |
| `runs/example-fake-data/` | Invented numbers so the dashboard renders on a fresh clone. Not a measurement. |
| `runs/<id>/config.json` | What the run used: models, `TOP_K`, chunk size, dataset checksum. |
| `runs/<id>/scores.csv` | One line per question: ids, status and scores. No question, answer or syllabus text. |
| `runs/<id>/summary.json` | The aggregated results the README table and dashboard read. |
| `runs/<id>/answers.jsonl` | Full answers including syllabus text. Gitignored, never committed. |
| `docs/dataset-review.md` | The per-row record of checking the pilot questions against the syllabus. |
| `docs/phase-2-findings.md` | What the pilot baseline showed, with the numbers behind each finding. |
| `DECISIONS.md` | Why things are the way they are, including what was measured rather than assumed. |
| `tests/conftest.py` | Shared fakes for the offline tests. Keeps this name because pytest requires it. |
| `scripts/smoke_test_phase_1.py` | The Phase 1 manual smoke test. |

`conftest.py` and `__main__.py` keep their names because pytest and Python
respectively require them; everything else is named for what it does.

## Evaluation

Phase 2 measures the Phase 1 pipeline exactly as it is — no prompt, chunking or
retrieval settings were changed.

**What is measured.** Each golden row is routed to the metrics that can mean
something for it. In-scope rows get context precision and context recall
(retrieval is scored even when the bot declined to answer, because that is
where retrieval failures show up). In-scope rows that *were* answered also get
faithfulness and response relevancy. Out-of-scope and not-in-syllabus rows get
no judge at all: they are correct if the bot refused or found no context, and a
not-in-syllabus row that was answered is flagged as a possible hallucination.

**Golden dataset:** LLM-drafted and LLM-verified against the syllabus pages
(see [docs/dataset-review.md](docs/dataset-review.md)); rows a human has checked
are marked `reviewed_by: human`. `data/golden_dataset.jsonl` holds 75 rows; the first
baseline runs on the 15 marked `"pilot": true`. The run refuses to start with
fewer than `MIN_REVIEWED_ROWS` reviewed rows.

**Running it:**

```bash
uv run python -m istqb_rag.eval all      --run-id pilot-1   # generate, score, report
uv run python -m istqb_rag.eval measure  --run-id pilot-1   # judge cost for one row
```

The stages are separate so answers are generated once and can be re-scored
later. Both resume: rerun the same command after a crash or a rate limit and it
picks up where it stopped.

### Baseline results (pilot, n=15)

Run `pilot-1` — answer model `openai/gpt-oss-120b`, judge `qwen/qwen3.8-27b`,
Ragas 0.4.3, `TOP_K=4`. All 15 rows scored. Numbers are exactly those in
`runs/pilot-1/summary.json`.

| Metric | Mean | Rows scored (n) | Expected | NaN (parse / truncated) |
|---|---|---|---|---|
| Context precision | 0.770 | 11 | 11 | 0 / 0 |
| Context recall | 0.778 | 11 | 11 | 0 / 0 |
| Faithfulness | 0.841 | 8 | 10 | 0 / 2 |
| Response relevancy | 0.869 | 10 | 10 | 0 / 0 |

| | value |
|---|---|
| In-scope answer rate | 90.9% (10/11) |
| Out-of-scope accuracy | 50% (1/2) — see finding 2, this is a measurement artifact |
| Not-in-syllabus accuracy | 100% (2/2) |
| Possible hallucinations | 0 |
| Errors | 0 |
| Latency | median 8829 ms, p95 12021 ms |

Two faithfulness verdicts (`q045`, `q048`) are missing because `JUDGE_MAX_TOKENS`
truncated the judge's reply, not because the judge produced anything unreadable.
`summary.json` counts those separately as `nan_truncated`, and they do not count
towards the run-validity guard. There were **no** unexplained parse failures.

At **pilot, n=15** most per-group means rest on one to three rows. Groups below
three rows are reported as "n too small" in both the console report and the
dashboard, and are left out of the dashboard charts. Read the pilot as a check
that the pipeline and the judge work end to end, not as evidence about a given
chapter or K-level.

Findings: `docs/phase-2-findings.md` (written after the pilot run).

## Roadmap

- **Phase 1:** ingest + single-turn RAG pipeline with citations
  and refusal, CLI and Streamlit chat.
- **Phase 2 (this phase):** golden dataset + Ragas evaluation runner scored
  against `answer()`, with an eval dashboard tab. First baseline is a 15-row
  pilot; the remaining 60 rows are kept for later runs.
- **Phase 3:** retrieval experiments (e.g. section-aware chunking) measured with
  Ragas.
- **Phase 4:** judge-model evaluation and final report.

## Notes

- `data/raw/`, `data/processed/` and `.chroma/` are gitignored; the PDF and the
  vector store are never committed.
- All model names, thresholds and sizes live in `.env` (see `.env.example`).
- Design choices and defaults are recorded in [DECISIONS.md](DECISIONS.md).

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

**Golden dataset:** LLM-drafted, human review pending. `data/golden.jsonl` holds
75 rows; the first baseline runs on the 15 marked `"pilot": true`. Every row is
`reviewed: false` until a human checks it against the syllabus page, and the run
refuses to start with fewer than `MIN_REVIEWED_ROWS` reviewed rows. This line
becomes "LLM-drafted, human-reviewed" once that pass is done.

**Running it:**

```bash
uv run python -m istqb_rag.eval all      --run-id pilot-1   # generate, score, report
uv run python -m istqb_rag.eval measure  --run-id pilot-1   # judge cost for one row
```

The stages are separate so answers are generated once and can be re-scored
later. Both resume: rerun the same command after a crash or a rate limit and it
picks up where it stopped.

### Baseline results (pilot, n=15)

Pending — fill from `runs/pilot-1/summary.json` after the pilot run.

| Metric | Mean | Rows scored | NaN |
|---|---|---|---|
| Context precision | — | — | — |
| Context recall | — | — | — |
| Faithfulness | — | — | — |
| Response relevancy | — | — | — |

At **pilot, n=15** most per-group means rest on two or three rows. Groups below
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

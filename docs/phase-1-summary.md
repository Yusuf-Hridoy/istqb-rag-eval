# Phase 1 summary

Phase 1 delivers the system under test: a plain, single-turn LangChain RAG
pipeline that answers only from the ISTQB CTFL v4.0 syllabus, plus a CLI and a
Streamlit chat tab. Evaluation (Ragas) arrives in Phase 2.

## What was built

- **Ingestion** (`src/istqb_rag/ingest.py`): PyMuPDF page load, content-page
  filtering, repeated header/footer line removal (with digit normalization),
  `RecursiveCharacterTextSplitter`, Chroma store with cosine distance.
  Re-running rebuilds the collection.
- **Pipeline** (`src/istqb_rag/pipeline.py`): `answer(question) -> RagResult`
  is the only entry point (UI, CLI and the future eval runner all call it).
  Retrieve top-k with relevance scores → relevance floor (`MIN_RELEVANCE`,
  no LLM call below it) → one grounded generation (temperature 0) with page
  citations → status mapped from the fixed refusal/not-found texts. Errors
  become `status="error"`; the function never raises.
- **CLI** (`src/istqb_rag/cli.py`): plain and `--json` output.
- **Streamlit app** (`app/streamlit_app.py`): Chat tab (history is UI-only,
  page badges, "Sources used" expander, sidebar examples/clear/model,
  ingest hint when the store is missing) and a Phase-2 placeholder Eval tab.
- **Tests**: 16 offline tests (fake LLM + in-memory Chroma), no keys/PDF/network.
- **Smoke test runner** (`scripts/smoke_test_phase_1.py`): writes
  `docs/smoke-test-phase-1.md`.

## Ingest summary (real PDF)

- Source: `ISTQB_CTFL_Syllabus_v4.0.1.pdf` (78 pages), content pages 14–75
  (62 pages; front matter + index excluded).
- **197 chunks**, average length **829 characters** (CHUNK_SIZE=1000,
  CHUNK_OVERLAP=150).
- Collection `ctfl_v4` in `.chroma/`, cosine distance, embeddings
  `BAAI/bge-small-en-v1.5`.

## Smoke test results

7 of 8 behave as expected (see `docs/smoke-test-phase-1.md` for full JSON):

- Q2–Q5 (defect vs failure, BVA, risk-based testing, static testing):
  `answered` with correct page citations.
- Q6–Q7 (Python framework, poem): `refused`. Q8 (exam fee): `no_context`,
  no fee invented.
- **Q1 (seven testing principles): `no_context` — known retrieval miss.**
  The principles list spans four continuation chunks that no pinned-stack
  configuration ranks into top-k; the honest answer is not-found. Full
  evidence and the models-that-were-tried in `DECISIONS.md`. This is the
  baseline weakness Phase 2 (Ragas) will quantify and Phase 3
  (section-aware chunking) is expected to fix.

## DECISIONS.md entries (all deviations/ambiguity calls)

1. Answer model `openai/gpt-oss-120b` — Groq no longer serves the brief's
   `llama-3.3-70b-versatile` (404 from the API itself).
2. Repo created at `~/Ragas-Test/istqb-rag-eval` (no clone existed).
3. Content page range 14–75, with code defaults in `config.py`.
4. Header/footer removal uses digit-normalized line matching (the brief's own
   rule, made to actually catch "Page N of 78").
5. Chunk ID scheme `p<page>-<n>` (1-based printed pages).
6. Refused/not-found render as `st.info` in the UI.
7. Streamlit store-missing check is the simple `.chroma/` non-empty proxy.
8. Q1 kept as an honest `no_context` with evidence (see above).

## Acceptance criteria status

- [x] `uv sync` + ingest builds the store from the real PDF, 197 chunks (>100).
- [x] `uv run pytest` passes offline (16 tests); `ruff check` and
      `ruff format --check` pass.
- [x] `git status` after a full run: no PDF, no `.chroma/`, no `.env` (all
      gitignored, verified before tagging).
- [x] CLI and Streamlit give the same result for the same question (both call
      `answer()`; Streamlit boot verified headless).
- [~] Smoke test: 7/8 as expected; Q1 is an honest, documented retrieval miss
      (see DECISIONS.md — kept rather than hacked green).
- [x] Everything pushed to `main`, tagged `phase-1`.

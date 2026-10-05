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

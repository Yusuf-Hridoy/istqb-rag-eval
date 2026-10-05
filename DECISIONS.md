# DECISIONS.md

Ambiguities in the Phase 1 brief resolved with the simplest option, as instructed.

## Repo location

The brief says the repo is "cloned locally" but no clone existed. The project
was created at `~/Ragas-Test/istqb-rag-eval` (inside the working directory that
held the PDF and `.env`), initialised with `git init -b main`, and pushed to a
new GitHub repo at the end of the phase.

## Content page range: 14–75

Inspecting `ISTQB_CTFL_Syllabus_v4.0.1.pdf` (78 pages, viewer page numbers):

- Pages 1–13 are cover, copyright, table of contents and the introduction
  (chapter 0). Page 14 starts "1. Fundamentals of Testing".
- Pages 76–78 are the index.
- The appendices (A–C) on pages ~64–75 are kept: they are part of the syllabus
  and the brief only excludes front matter, TOC and index.

So `FIRST_CONTENT_PAGE=14`, `LAST_CONTENT_PAGE=75` (defaults in `config.py`,
overridable via `.env`).

## Defaults in config.py

`FIRST_CONTENT_PAGE`/`LAST_CONTENT_PAGE` have code defaults (14/75) rather than
empty, so a fresh clone without a tailored `.env` ingests the right range for
this exact PDF. Everything else defaults as in `.env.example`.

## Header/footer removal

Any non-empty line occurring on more than half of the kept pages is dropped
(exact string match after stripping). For this PDF that removes "Certified
Tester", "Foundation Level", "v4.0.1", "Page N of 78", "2024-09-15" and the
copyright line. Page numbers survive because each page's number differs.

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

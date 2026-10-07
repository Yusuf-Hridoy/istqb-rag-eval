"""Streamlit chat UI for the ISTQB CTFL assistant.

Run with: uv run streamlit run app/streamlit_app.py

Chat only. Evaluation is a command-line job that writes plain report files to
runs/<run-id>/report.md.
"""

import re
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from istqb_rag.config import get_settings  # noqa: E402
from istqb_rag.pipeline import answer  # noqa: E402

EXAMPLE_QUESTIONS = [
    "What are the seven testing principles?",
    "What is the difference between a defect and a failure?",
    "Explain boundary value analysis with an example.",
    "What does risk-based testing involve?",
]

# A sentence ending followed by whitespace. Used only to tidy what is shown.
_SENTENCE_END = re.compile(r"[.!?][\"')\]]?\s")


@st.cache_resource
def get_pipeline():
    """Load settings once; the store is opened per question inside answer()."""
    return get_settings()


@st.cache_resource
def get_scorer():
    """One Ragas scorer for the whole session, reused from the eval code."""
    from istqb_rag.eval.step2_judge_scores import make_scorer

    return make_scorer()


def trim_to_sentences(text: str) -> str:
    """Trim a chunk to whole sentences, for display only.

    A retrieved chunk is cut by character count, so it often starts mid-sentence
    and ends mid-word. This drops the dangling halves so a quoted source reads
    properly. The model still receives the untrimmed chunk — this never changes
    what was retrieved or sent.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""

    body = cleaned
    if body[:1].islower():  # the sentence began in the previous chunk
        first = _SENTENCE_END.search(body)
        if first:
            body = body[first.end() :]

    if body and body[-1] not in ".!?":  # the last sentence was cut off
        ends = [m.end() for m in _SENTENCE_END.finditer(body)]
        if ends:
            body = body[: ends[-1]]

    return body.strip() or cleaned


def cited_chunks(contexts, cited_pages):
    """Only the retrieved chunks whose page the answer actually cited."""
    cited = set(cited_pages or [])
    return [c for c in contexts if c.page in cited]


def score_caption(values: dict) -> str:
    """One line: the two scores a reference-free judge can produce."""

    def fmt(key):
        value = values.get(key)
        return "—" if value is None else f"{value:.2f}"

    return f"Judge: faithfulness {fmt('faithfulness')} · relevance {fmt('response_relevancy')}"


def _judge_available(settings) -> bool:
    """The judge needs whichever key its provider uses."""
    import os

    if settings.judge_model.startswith("gemini"):
        return bool(os.environ.get("GEMINI_API_KEY"))
    return bool(settings.groq_api_key)


def _render_score(result, settings) -> None:
    """Score an answered reply, when scoring is on and a judge key exists."""
    if result.status != "answered":
        return  # a refusal or a not-found has nothing to be faithful to
    if not st.session_state.get("auto_score", True):
        return
    if not _judge_available(settings):
        st.caption("Scoring is off: set GROQ_API_KEY in .env to enable it.")
        return

    with st.spinner("Asking the judge…"):
        outcome = get_scorer()(
            result.question,
            result.answer,
            None,
            [c.text for c in result.contexts],
            ["faithfulness", "response_relevancy"],
        )
    if outcome.api_error:
        st.caption(f"Judge call failed: {outcome.api_error}")
        return
    st.caption(score_caption(outcome.values))


def _render_result(result) -> None:
    if result.status in ("refused", "no_context"):
        st.info(result.answer)
    elif result.error:
        st.error(f"Error: {result.error}")
    else:
        st.markdown(result.answer)

    if result.cited_pages:
        st.markdown(" ".join(f"`p. {p}`" for p in result.cited_pages))

    for chunk in cited_chunks(result.contexts, result.cited_pages):
        with st.expander(f"Source · p. {chunk.page}"):
            st.caption(trim_to_sentences(chunk.text))


def main() -> None:
    st.set_page_config(page_title="ISTQB CTFL Assistant")
    st.title("ISTQB CTFL Assistant")
    settings = get_pipeline()

    with st.sidebar:
        st.caption(f"Model: {settings.answer_model}")
        st.toggle("Score answers automatically", key="auto_score", value=True)
        st.caption("Each score uses ~3 judge calls.")
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, key=f"example-{q}"):
                st.session_state["pending_question"] = q
        if st.button("Clear chat"):
            st.session_state["messages"] = []
            st.session_state.pop("pending_question", None)
            st.rerun()

    store_ready = settings.chroma_dir.exists() and any(settings.chroma_dir.iterdir())
    if not store_ready:
        st.warning(
            "The syllabus has not been ingested yet. Run:\n\n"
            "`uv run python -m istqb_rag.ingest`\n\n"
            "then refresh this page."
        )
        return

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                _render_result(message["result"])  # prints the answer itself
            else:
                st.markdown(message["content"])

    question = st.chat_input("Ask about the ISTQB CTFL syllabus…")
    if not question and "pending_question" in st.session_state:
        question = st.session_state.pop("pending_question")

    if question:
        st.session_state["messages"].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            result = answer(question)  # single-turn: no history is passed
            _render_result(result)
            _render_score(result, settings)
        st.session_state["messages"].append(
            {"role": "assistant", "content": result.answer, "result": result}
        )


if __name__ == "__main__":
    main()

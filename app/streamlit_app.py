"""Streamlit UI for the ISTQB CTFL assistant.

Run with: uv run streamlit run app/streamlit_app.py
"""

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


@st.cache_resource
def get_pipeline():
    """Load settings once; the store is opened per question inside answer()."""
    return get_settings()


def main() -> None:
    st.set_page_config(page_title="ISTQB CTFL Assistant")
    st.title("ISTQB CTFL Assistant")
    settings = get_pipeline()

    with st.sidebar:
        st.caption(f"Model: {settings.answer_model}")
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, key=f"example-{q}"):
                st.session_state["pending_question"] = q
        if st.button("Clear chat"):
            st.session_state["messages"] = []
            st.session_state.pop("pending_question", None)
            st.rerun()

    chat_tab, eval_tab = st.tabs(["Chat", "Eval dashboard"])
    with eval_tab:
        st.info("The eval dashboard arrives in Phase 2 (Ragas).")

    with chat_tab:
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
                st.markdown(message["content"])
                if message["role"] == "assistant":
                    _render_result(message["result"])

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
            st.session_state["messages"].append(
                {"role": "assistant", "content": result.answer, "result": result}
            )


def _render_result(result) -> None:
    if result.status in ("refused", "no_context"):
        st.info(result.answer)
    elif result.error:
        st.error(f"Error: {result.error}")
    else:
        st.markdown(result.answer)
    if result.cited_pages:
        st.markdown(" ".join(f"`p. {p}`" for p in result.cited_pages))
    with st.expander("Sources used"):
        for chunk in result.contexts:
            st.markdown(f"**p. {chunk.page}** — relevance {chunk.score:.2f}")
            st.caption(chunk.text)
    st.caption(
        f"status: {result.status} · latency: {result.latency_ms['total']} ms"
        f" · model: {result.model}"
    )


if __name__ == "__main__":
    main()

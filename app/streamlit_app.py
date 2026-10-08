"""Streamlit chat UI for the ISTQB CTFL assistant."""

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

_SENTENCE_END = re.compile(r"[.!?][\"')\]]?\s")


@st.cache_resource
def get_pipeline():
    """Load settings once; the store is opened per question inside answer()."""
    return get_settings()


@st.cache_resource
def get_scorer():
    from istqb_rag.eval.step2_judge_scores import make_scorer

    return make_scorer()


def trim_to_sentences(text: str) -> str:
    """Trim a chunk to whole sentences for display; the model still gets the full chunk."""
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


SCORE_MEANINGS = (
    ("faithfulness", "faithfulness", "Every claim is backed by the source"),
    ("response_relevancy", "relevance", "The answer addresses the question"),
)
OK_LABEL = "OK"
CHECK_LABEL = "Check this answer"


def judge_verdict(values: dict, threshold: float) -> dict:
    """Label two judge scores; a missing score is a reason to check, never a pass."""
    missing = [label for key, label, _ in SCORE_MEANINGS if values.get(key) is None]
    if missing:
        return {"label": CHECK_LABEL, "ok": False, "missing": missing}
    ok = all(values[key] >= threshold for key, _, _ in SCORE_MEANINGS)
    return {"label": OK_LABEL if ok else CHECK_LABEL, "ok": ok, "missing": []}


def score_caption(values: dict) -> str:
    def fmt(key):
        value = values.get(key)
        return "—" if value is None else f"{value:.2f}"

    return f"Judge: faithfulness {fmt('faithfulness')} · relevance {fmt('response_relevancy')}"


def _judge_available(settings) -> bool:
    import os

    if settings.judge_model.startswith("gemini"):
        return bool(os.environ.get("GEMINI_API_KEY"))
    return bool(settings.groq_api_key)


def _render_score(result, settings) -> None:
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
    _render_judge_box(outcome.values, settings.score_ok_threshold)


def _render_judge_box(values: dict, threshold: float) -> None:
    """The Judge box; the label is always written out, so colour is never the only signal."""
    verdict = judge_verdict(values, threshold)
    lines = [f"**Judge: {verdict['label']}**", ""]
    for key, label, meaning in SCORE_MEANINGS:
        value = values.get(key)
        shown = "not scored" if value is None else f"{value:.2f}"
        lines.append(f"- {label} {shown} — {meaning}")
    if verdict["missing"]:
        lines.append("")
        lines.append(
            "The judge's reply was cut off, so "
            + " and ".join(verdict["missing"])
            + (" was" if len(verdict["missing"]) == 1 else " were")
            + " not scored."
        )
    box = st.info if verdict["ok"] else st.warning
    box("\n".join(lines))
    st.caption(
        f"OK: both scores at or above {threshold:.2f} · "
        "Check: read it against its source. The judge is an AI and can be wrong."
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
                _render_result(message["result"])
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

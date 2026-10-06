"""Streamlit UI for the ISTQB CTFL assistant.

Run with: uv run streamlit run app/streamlit_app.py
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from istqb_rag.config import get_settings  # noqa: E402
from istqb_rag.eval.step2_judge_scores import METRIC_KEYS  # noqa: E402
from istqb_rag.eval.step3_build_summary import MIN_GROUP_N  # noqa: E402
from istqb_rag.pipeline import answer  # noqa: E402

EXAMPLE_QUESTIONS = [
    "What are the seven testing principles?",
    "What is the difference between a defect and a failure?",
    "Explain boundary value analysis with an example.",
    "What does risk-based testing involve?",
]

METRIC_LABELS = {
    "context_precision": "Context precision",
    "context_recall": "Context recall",
    "faithfulness": "Faithfulness",
    "response_relevancy": "Response relevancy",
}


@st.cache_resource
def get_pipeline():
    """Load settings once; the store is opened per question inside answer()."""
    return get_settings()


# --- Eval dashboard ---------------------------------------------------------


def _list_runs(settings) -> list[str]:
    """Run ids that have a committed summary, newest first."""
    if not settings.runs_dir.exists():
        return []
    runs = [
        d.name for d in settings.runs_dir.iterdir() if d.is_dir() and (d / "summary.json").exists()
    ]
    return sorted(
        runs, key=lambda n: (settings.runs_dir / n / "summary.json").stat().st_mtime, reverse=True
    )


@st.cache_data
def _load_run(runs_dir_str: str, run_id: str):
    run_dir = Path(runs_dir_str) / run_id
    summary = json.loads((run_dir / "summary.json").read_text())
    config = {}
    if (run_dir / "config.json").exists():
        config = json.loads((run_dir / "config.json").read_text())
    scores = pd.read_csv(run_dir / "scores.csv")
    return summary, config, scores


@st.cache_data
def _load_questions(golden_path_str: str) -> dict[str, str]:
    """Question text by id, for the Worst 10 table (scores.csv holds no text)."""
    path = Path(golden_path_str)
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["id"]] = row["question"]
    return out


def _metric_card(column, key: str, stats: dict) -> None:
    mean = stats.get("mean")
    column.metric(METRIC_LABELS[key], f"{mean:.3f}" if mean is not None else "—")
    column.caption(f"{stats.get('scored', 0)} rows scored · {stats.get('nan', 0)} NaN")


def _group_table(groups: dict) -> pd.DataFrame:
    """Group means with n, greying out any group too thin to read."""
    data = {}
    for group, stats in groups.items():
        label = f"{group} (n={stats['n']})"
        if stats.get("n_too_small"):
            data[label] = dict.fromkeys(METRIC_LABELS.values(), "n too small")
        else:
            data[label] = {
                METRIC_LABELS[key]: (
                    f"{stats[key]['mean']:.3f}" if stats[key]["mean"] is not None else "—"
                )
                for key in METRIC_KEYS
            }
    return pd.DataFrame(data)


def _grey_small_n(frame: pd.DataFrame):
    return frame.style.map(
        lambda v: "color: #999; font-style: italic" if v == "n too small" else ""
    )


def _group_section(summary: dict, section: str, title: str) -> None:
    """A bar chart of the groups with enough rows, plus a table carrying every n."""
    groups = summary.get(section, {})
    if not groups:
        return
    st.caption(title)

    big = {g: s for g, s in groups.items() if not s.get("n_too_small")}
    if big:
        st.bar_chart(
            pd.DataFrame(
                {
                    METRIC_LABELS[key]: {
                        f"{g} (n={s['n']})": s[key]["mean"] for g, s in big.items()
                    }
                    for key in METRIC_KEYS
                }
            )
        )
    thin = [g for g, s in groups.items() if s.get("n_too_small")]
    if thin:
        st.caption(
            f"Not charted, fewer than {MIN_GROUP_N} rows: "
            + ", ".join(f"{g} (n={groups[g]['n']})" for g in thin)
        )
    st.dataframe(_grey_small_n(_group_table(groups)), use_container_width=True)


def render_eval_tab(settings) -> None:
    runs = _list_runs(settings)
    if not runs:
        st.info(
            "No eval runs yet. Generate one with:\n\n"
            "```\nuv run python -m istqb_rag.eval all --run-id baseline\n```"
        )
        return

    run_id = st.selectbox("Run", runs, index=0)
    summary, config, scores = _load_run(str(settings.runs_dir), run_id)

    if config.get("fixture"):
        st.warning(
            "This is the **fixture** run: invented numbers so the dashboard renders "
            "on a fresh clone. It is not a real measurement."
        )
    st.caption(
        f"answer: {config.get('answer_model', '?')} · judge: {config.get('judge_model', '?')} · "
        f"{config.get('row_count', len(scores))} rows · {str(config.get('timestamp', ''))[:10]}"
    )

    cols = st.columns(4)
    for col, key in zip(cols, METRIC_KEYS, strict=True):
        _metric_card(col, key, summary["overall"][key])

    rate = summary["in_scope_answer_rate"]
    scope = summary["scope_handling"]
    errors = summary["errors"]
    second = st.columns(4)
    second[0].metric("In-scope answer rate", f"{(rate['rate'] or 0) * 100:.1f}%")
    second[0].caption(f"{rate['answered']}/{rate['total']} answered")
    second[1].metric(
        "Out-of-scope accuracy",
        f"{(scope['out_of_scope_accuracy'] or 0) * 100:.1f}%",
    )
    second[1].caption(f"{scope['out_of_scope_total']} rows")
    second[2].metric(
        "Not-in-syllabus accuracy",
        f"{(scope['not_in_syllabus_accuracy'] or 0) * 100:.1f}%",
    )
    second[2].caption(f"{scope['not_in_syllabus_total']} rows")
    second[3].metric("Errors", errors["count"])
    second[3].caption(f"{errors['error_rate'] * 100:.1f}% of rows")

    if scope["possible_hallucination_ids"]:
        st.warning(
            "Possible hallucinations (not-in-syllabus rows that were answered): "
            + ", ".join(scope["possible_hallucination_ids"])
        )
    if rate["not_answered_ids"]:
        st.info("False refusals (in-scope, not answered): " + ", ".join(rate["not_answered_ids"]))

    _group_section(summary, "by_chapter", "Metrics by chapter")
    _group_section(summary, "by_k_level", "Metrics by K-level")

    st.caption("Multi-chunk answers vs single-chunk")
    multi = summary.get("by_multi_chunk", {})
    if multi:
        renamed = {
            ("multi-chunk" if group == "True" else "single-chunk"): stats
            for group, stats in multi.items()
        }
        st.dataframe(_grey_small_n(_group_table(renamed)), use_container_width=True)

    st.caption("Worst 10 in-scope rows")
    worst = pd.DataFrame(summary["worst_10"])
    if not worst.empty:
        questions = _load_questions(str(settings.golden_path))
        worst.insert(1, "question", worst["id"].map(questions).fillna(""))
        st.dataframe(worst, use_container_width=True, hide_index=True)

    lat = summary["latency_ms"]
    st.caption(f"Latency: median {lat['median']:.0f} ms · p95 {lat['p95']:.0f} ms")


# --- Chat -------------------------------------------------------------------


def _judge_available(settings) -> bool:
    """The live score button needs whichever key the configured judge uses."""
    import os

    if settings.judge_model.startswith("gemini"):
        return bool(os.environ.get("GEMINI_API_KEY"))
    return bool(settings.groq_api_key)


@st.cache_resource
def get_scorer():
    """One Ragas scorer for the whole session, reused from step2_judge_scores.py."""
    from istqb_rag.eval.step2_judge_scores import make_scorer

    return make_scorer()


def _render_score_button(result, key: str, settings) -> None:
    if result.status != "answered":
        return
    if not _judge_available(settings):
        st.caption(
            "Scoring is off: no judge API key set. Add GROQ_API_KEY (or GEMINI_API_KEY "
            "for a Gemini judge) to .env to enable it."
        )
        return

    if st.button("Score this answer", key=f"score-{key}"):
        with st.spinner("Asking the judge…"):
            outcome = get_scorer()(
                result.question,
                result.answer,
                None,
                [c.text for c in result.contexts],
                ["faithfulness", "response_relevancy"],
            )
        if outcome.api_error:
            st.error(f"Judge call failed: {outcome.api_error}")
            return
        left, right = st.columns(2)
        for col, metric in ((left, "faithfulness"), (right, "response_relevancy")):
            value = outcome.values.get(metric)
            col.metric(METRIC_LABELS[metric], f"{value:.3f}" if value is not None else "—")
    st.caption(
        "Only faithfulness and response relevancy: with no reference answer, "
        "context precision and recall cannot be computed."
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


def render_chat_tab(settings) -> None:
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

    for i, message in enumerate(st.session_state["messages"]):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                _render_result(message["result"])
                _render_score_button(message["result"], str(i), settings)

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
        st.rerun()  # re-render so the new reply gets its score button


def main() -> None:
    st.set_page_config(page_title="ISTQB CTFL Assistant", layout="wide")
    st.title("ISTQB CTFL Assistant")
    settings = get_pipeline()

    with st.sidebar:
        st.caption(f"Model: {settings.answer_model}")
        st.caption(f"Judge: {settings.judge_model}")
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, key=f"example-{q}"):
                st.session_state["pending_question"] = q
        if st.button("Clear chat"):
            st.session_state["messages"] = []
            st.session_state.pop("pending_question", None)
            st.rerun()

    chat_tab, eval_tab = st.tabs(["Chat", "Eval dashboard"])
    with eval_tab:
        render_eval_tab(settings)
    with chat_tab:
        render_chat_tab(settings)


if __name__ == "__main__":
    main()

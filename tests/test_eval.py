"""Offline tests for eval scoring, routing, resume and reporting."""

import math

from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.eval.generate import run_generate
from istqb_rag.eval.report import build_summary
from istqb_rag.eval.score import (
    METRIC_KEYS,
    SCORES_COLUMNS,
    metrics_for,
    run_score,
    scope_verdict,
)
from istqb_rag.models import RagResult, RetrievedChunk
from tests.conftest import make_settings


def _row(row_id="q001", row_type="in_scope", status_answered=True):
    return GoldenRow(
        id=row_id,
        question=f"question {row_id}?",
        reference="reference answer" if row_type == "in_scope" else None,
        reference_pages=[17] if row_type == "in_scope" else [],
        chapter=1 if row_type == "in_scope" else None,
        section="1.3" if row_type == "in_scope" else None,
        k_level="K2" if row_type == "in_scope" else None,
        type=row_type,
        multi_chunk=False,
        source="llm",
        reviewed=True,
    )


def _result(status="answered", row_id="q001"):
    return RagResult(
        question=f"question {row_id}?",
        status=status,
        answer="some answer"
        if status == "answered"
        else "I couldn't find this in the ISTQB CTFL syllabus.",
        contexts=[RetrievedChunk(chunk_id="p17-1", page=17, text="context text", score=0.8)],
        cited_pages=[17] if status == "answered" else [],
        model="fake",
        latency_ms={"retrieve": 10, "generate": 20, "total": 30},
    )


def test_metrics_for_routing_table():
    assert metrics_for("in_scope", "answered") == METRIC_KEYS
    assert metrics_for("in_scope", "refused") == ["context_precision", "context_recall"]
    assert metrics_for("in_scope", "no_context") == ["context_precision", "context_recall"]
    assert metrics_for("in_scope", "error") == []
    assert metrics_for("not_in_syllabus", "answered") == []
    assert metrics_for("out_of_scope", "refused") == []


def test_scope_verdict():
    assert scope_verdict("out_of_scope", "refused") == (True, "")
    assert scope_verdict("out_of_scope", "no_context") == (True, "")
    assert scope_verdict("out_of_scope", "answered") == (False, "")
    assert scope_verdict("not_in_syllabus", "no_context") == (True, "")
    assert scope_verdict("not_in_syllabus", "refused") == (True, "")
    assert scope_verdict("not_in_syllabus", "answered") == (False, "possible_hallucination")
    assert scope_verdict("in_scope", "answered") == (None, "")


def test_generate_resume_skips_saved_rows(tmp_path):
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002")]
    calls = []

    def fake_answer(question):
        calls.append(question)
        return _result(row_id="q" + question.split()[-1].strip("?"))

    stats = run_generate("test-run", rows, fake_answer, settings=settings)
    assert stats["generated"] == 2

    calls.clear()
    stats = run_generate("test-run", rows, fake_answer, settings=settings)
    assert stats == {"generated": 0, "skipped": 2}
    assert calls == []  # the answer fn was never called for saved rows


def test_run_score_writes_committed_columns_only(tmp_path):
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002", row_type="out_of_scope")]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    def fake_scorer(question, response, reference, contexts, metric_keys):
        assert reference == "reference answer"
        assert contexts == ["context text"]
        return {k: 0.5 for k in metric_keys}

    stats = run_score("test-run", rows, fake_scorer, settings=settings)
    assert stats["scored"] == 2
    assert stats["judged"] == 1  # only the in_scope row gets judged

    import csv

    with (settings.runs_dir / "test-run" / "scores.csv").open() as f:
        saved = list(csv.DictReader(f))
    assert list(saved[0].keys()) == SCORES_COLUMNS
    assert saved[0]["context_precision"] == "0.5000"
    assert saved[0]["faithfulness"] == "0.5000"
    assert saved[1]["context_precision"] == ""  # out_of_scope: no metrics
    assert saved[1]["possible_hallucination"] == ""
    # no question or answer text leaks into scores.csv
    text = (settings.runs_dir / "test-run" / "scores.csv").read_text()
    assert "question q001" not in text and "some answer" not in text


def _score_row(
    row_id,
    chapter="1",
    k_level="K2",
    multi=False,
    status="answered",
    cp=0.8,
    cr=0.8,
    f=0.8,
    rr=0.8,
    row_type="in_scope",
    latency=100,
):
    return {
        "id": row_id,
        "type": row_type,
        "chapter": chapter,
        "k_level": k_level,
        "multi_chunk": multi,
        "status": status,
        "cited_pages": "17",
        "best_score": 0.8,
        "context_precision": cp,
        "context_recall": cr,
        "faithfulness": f,
        "response_relevancy": rr,
        "latency_ms": latency,
        "possible_hallucination": "",
    }


def test_build_summary_groups_and_nans():
    rows = [
        _score_row("q001", chapter="1", cp=1.0, cr=1.0),
        _score_row("q002", chapter="1", k_level="K1", cp=0.0, cr=0.0, f=0.0, rr=0.0),
        _score_row("q003", chapter="2", multi=True, status="no_context", cp=math.nan, cr=0.5),
        _score_row(
            "q004",
            row_type="out_of_scope",
            status="refused",
            cp=math.nan,
            cr=math.nan,
            f=math.nan,
            rr=math.nan,
        ),
        _score_row(
            "q005",
            row_type="not_in_syllabus",
            status="answered",
            cp=math.nan,
            cr=math.nan,
            f=math.nan,
            rr=math.nan,
        ),
        _score_row(
            "q006", status="error", cp=math.nan, cr=math.nan, f=math.nan, rr=math.nan, latency=0
        ),
    ]
    summary = build_summary(rows)

    overall = summary["overall"]["context_precision"]
    assert overall["mean"] == 0.5  # (1.0 + 0.0) / 2, NaN excluded
    assert overall["scored"] == 2
    assert overall["nan"] == 4

    ch1 = summary["by_chapter"]["1"]["context_precision"]
    assert ch1["mean"] == 0.5 and ch1["scored"] == 2
    assert summary["by_chapter"]["2"]["context_precision"]["nan"] == 1

    rate = summary["in_scope_answer_rate"]
    assert rate["answered"] == 2 and rate["total"] == 4
    assert set(rate["not_answered_ids"]) == {"q003"}  # q006 is an error, counted separately

    scope = summary["scope_handling"]
    assert scope["out_of_scope_accuracy"] == 1.0
    assert scope["not_in_syllabus_accuracy"] == 0.0
    assert scope["possible_hallucination_ids"] == ["q005"]

    assert summary["errors"]["count"] == 1
    assert summary["errors"]["error_rate"] == round(1 / 6, 4)
    assert summary["errors"]["error_rate_ok"] is False  # 1/6 = 16.7% > 5%
    assert summary["worst_10"][0]["id"] == "q002"
    assert summary["latency_ms"]["median"] == 100


def test_error_threshold_fails_run():
    rows = [
        _score_row(f"q{i:03d}", status="error", cp=math.nan, cr=math.nan, f=math.nan, rr=math.nan)
        for i in range(4)
    ]
    rows += [_score_row("q100", status="answered")]
    summary = build_summary(rows)
    assert summary["errors"]["count"] == 4
    assert summary["errors"]["error_rate_ok"] is False  # 4/5 = 80% > 5%

"""Offline tests for the plain-English run report and comparison."""

import json
from pathlib import Path

from istqb_rag.eval.plain_report import build_comparison, build_report, questions_by_id

QUESTIONS = {"q001": "What is testing?", "q007": "What are the seven principles?"}


def _summary(**overrides):
    overall = {
        key: {"mean": 0.8, "scored": 10, "unmeasured": False}
        for key in ("context_precision", "context_recall", "faithfulness", "response_relevancy")
    }
    base = {
        "overall": overall,
        "in_scope_answer_rate": {"rate": 0.9, "answered": 9, "total": 10},
        "errors": {"count": 0},
        "worst_10": [{"id": "q007", "avg": 0.1}, {"id": "q001", "avg": 0.5}],
    }
    base.update(overrides)
    return base


def _rows():
    return [
        {"id": "q001", "type": "in_scope", "status": "answered", "cited_pages": "15"},
        {"id": "q007", "type": "in_scope", "status": "answered", "cited_pages": ""},
        {"id": "q061", "type": "out_of_scope", "status": "refused", "cited_pages": ""},
        {"id": "q062", "type": "not_in_syllabus", "status": "answered", "cited_pages": ""},
    ]


def test_report_leads_with_the_run_description():
    config = {"description": "The baseline run.", "answer_model": "m", "top_k": 4}
    report = build_report("pilot-1", config, _summary(), _rows(), QUESTIONS)
    assert report.startswith("# pilot-1\n\nThe baseline run.")


def test_report_says_so_when_a_description_is_missing():
    report = build_report("r", {}, _summary(), _rows(), QUESTIONS)
    assert "No description recorded" in report


def test_every_judge_score_has_a_plain_meaning():
    report = build_report("r", {}, _summary(), _rows(), QUESTIONS)
    for meaning in (
        "how much of what was retrieved was actually relevant",
        "how much of the reference answer the retrieved text covered",
        "how much of the answer is backed by the retrieved text",
        "how directly the answer addresses the question",
    ):
        assert meaning in report


def test_an_unmeasured_metric_says_so_rather_than_showing_a_number():
    summary = _summary()
    summary["overall"]["faithfulness"] = {"mean": None, "scored": 0, "unmeasured": True}
    report = build_report("r", {}, summary, _rows(), QUESTIONS)
    assert "not measured in this run" in report


def test_report_states_behaviour_in_plain_numbers():
    report = build_report("r", {}, _summary(), _rows(), QUESTIONS)
    assert "**Answered** 9 of 10 syllabus questions (90%)." in report
    assert "**Cited a page** on 1 of 2 answers (50%)." in report
    assert "100% of 1 off-topic questions" in report  # q061 refused
    assert "0% of 1 testing questions" in report  # q062 answered when it should not have


def test_weakest_questions_carry_their_text():
    report = build_report("r", {}, _summary(), _rows(), QUESTIONS)
    assert "What are the seven principles?" in report
    assert "`q007`" in report


def test_runs_predating_the_switches_report_their_real_behaviour():
    """An absent chunking/answer_format key means the original behaviour."""
    report = build_report("pilot-1", {"top_k": 4}, _summary(), _rows(), QUESTIONS)
    assert "Chunking: page · answer format: text · prompt version: 1" in report


def test_questions_by_id_reads_the_committed_dataset(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text(json.dumps({"id": "q001", "question": "What is testing?"}) + "\n")
    assert questions_by_id(path) == {"q001": "What is testing?"}
    assert questions_by_id(tmp_path / "missing.jsonl") == {}


def _comparison_data():
    return {
        "rows_compared": 2,
        "metrics": {
            "context_precision": {
                "base_mean": 0.8,
                "base_n": 2,
                "new_mean": None,
                "new_n": 0,
                "delta": None,
            },
            "context_recall": {
                "base_mean": 0.8,
                "base_n": 2,
                "new_mean": 0.6,
                "new_n": 2,
                "delta": -0.2,
            },
            "faithfulness": {
                "base_mean": None,
                "base_n": 0,
                "new_mean": None,
                "new_n": 0,
                "delta": None,
            },
            "response_relevancy": {
                "base_mean": None,
                "base_n": 0,
                "new_mean": None,
                "new_n": 0,
                "delta": None,
            },
        },
        "citation_rate": {"base": {"rate": 0.8}, "new": {"rate": 1.0}},
        "out_of_scope_accuracy": {"base": (0.5, 2), "new": (1.0, 2)},
        "answer_rate": {"base": (0.9, 10), "new": (0.9, 10)},
        "changed_rows": [
            {
                "id": "q007",
                "status_changed": True,
                "base_status": "answered",
                "new_status": "no_context",
                "moved": {"context_recall": {"base": 0.8, "new": 0.0, "delta": -0.8}},
            }
        ],
    }


def test_comparison_shows_both_runs_and_the_change():
    out = build_comparison("pilot-1", "exp1", _comparison_data(), QUESTIONS)
    assert out.startswith("# exp1 compared with pilot-1")
    assert "0.80 (n=2) | 0.60 (n=2) | -0.20" in out
    assert "not measured" in out


def test_comparison_names_the_questions_that_changed():
    out = build_comparison("pilot-1", "exp1", _comparison_data(), QUESTIONS)
    assert "What are the seven principles?" in out
    assert "answered → no_context" in out


def test_comparison_says_plainly_when_nothing_changed():
    data = _comparison_data()
    data["changed_rows"] = []
    out = build_comparison("pilot-1", "exp1", data, QUESTIONS)
    assert "No question changed status or moved by more than 0.2." in out


def test_every_run_has_a_generated_report():
    runs = Path(__file__).resolve().parents[1] / "runs"
    for run in sorted(p for p in runs.iterdir() if p.is_dir()):
        assert (run / "report.md").exists(), f"{run.name} has no report.md"

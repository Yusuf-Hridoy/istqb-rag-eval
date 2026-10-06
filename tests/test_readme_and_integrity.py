"""Offline tests for the generated README tables and the CI results-integrity gate."""

import csv
import json
import subprocess
import sys
from pathlib import Path

from istqb_rag.eval.readme_tables import apply_tables, marker, missing_markers
from istqb_rag.eval.step2_judge_scores import SCORES_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_results_integrity.py"


# --- Generated README tables ------------------------------------------------


def test_apply_tables_fills_the_marked_block():
    readme = f"# Title\n\n{marker('baseline')}\nold numbers\n{marker('baseline', True)}\n\nend\n"
    out = apply_tables(readme, {"baseline": "| new | numbers |"})
    assert "| new | numbers |" in out
    assert "old numbers" not in out
    assert out.startswith("# Title") and out.endswith("end\n")


def test_apply_tables_is_idempotent():
    """Running the generator twice must not change the file again."""
    readme = f"{marker('baseline')}\nx\n{marker('baseline', True)}\n"
    once = apply_tables(readme, {"baseline": "table"})
    assert apply_tables(once, {"baseline": "table"}) == once


def test_a_hand_edited_number_does_not_survive_regeneration():
    """The whole point of the gate: typed numbers get overwritten and detected."""
    tables = {"baseline": "| Context recall | 0.778 |"}
    generated = apply_tables(f"{marker('baseline')}\n\n{marker('baseline', True)}\n", tables)
    tampered = generated.replace("0.778", "0.999")
    assert tampered != generated  # the edit landed
    assert apply_tables(tampered, tables) == generated  # and is reverted
    assert apply_tables(tampered, tables) != tampered  # i.e. CI would see a diff


def test_missing_markers_are_reported():
    assert missing_markers("no markers here", {"baseline": "x"}) == ["baseline"]
    complete = f"{marker('baseline')}{marker('baseline', True)}"
    assert missing_markers(complete, {"baseline": "x"}) == []


def test_half_a_marker_pair_counts_as_missing():
    assert missing_markers(marker("baseline"), {"baseline": "x"}) == ["baseline"]


def test_the_real_readme_is_in_sync_with_the_run_files():
    """Same assertion CI makes, so a stale README fails locally too."""
    from istqb_rag.eval.readme_tables import build_tables

    readme_path = REPO_ROOT / "README.md"
    original = readme_path.read_text(encoding="utf-8")
    tables = build_tables()
    assert missing_markers(original, tables) == []
    assert apply_tables(original, tables) == original


# --- The integrity check itself ---------------------------------------------


def _run_check(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_check_passes_on_the_real_repo():
    result = _run_check(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout


def _fake_repo(tmp_path: Path) -> Path:
    """A miniature repo the check script can be pointed at."""
    (tmp_path / "runs" / "run-a").mkdir(parents=True)
    for name in ("config.json", "summary.json"):
        (tmp_path / "runs" / "run-a" / name).write_text("{}", encoding="utf-8")
    with (tmp_path / "runs" / "run-a" / "scores.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCORES_COLUMNS)
        writer.writeheader()
    return tmp_path


def test_check_functions_detect_each_problem(tmp_path, monkeypatch):
    """Drive the check's functions directly against a fake repo."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("integrity_check", CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    repo = _fake_repo(tmp_path)
    monkeypatch.setattr(module, "REPO_ROOT", repo)

    # 1. a run directory missing summary.json
    (repo / "runs" / "run-a" / "summary.json").unlink()
    problems: list[str] = []
    module.check_runs_complete(problems)
    assert any("run-a" in p and "summary.json" in p for p in problems)

    # 2. a forbidden scores.csv column
    (repo / "runs" / "run-a" / "summary.json").write_text("{}", encoding="utf-8")
    scores = repo / "runs" / "run-a" / "scores.csv"
    scores.write_text("id,question,status\nq1,what is testing?,answered\n", encoding="utf-8")
    problems = []
    module.check_scores_columns(problems)
    assert any("question" in p for p in problems)
    assert any("non-allowlisted" in p for p in problems)

    # 3. the allowlisted header passes
    with scores.open("w", newline="") as f:
        csv.DictWriter(f, fieldnames=SCORES_COLUMNS).writeheader()
    problems = []
    module.check_scores_columns(problems)
    assert problems == []


def test_reviewed_row_without_reviewed_by_fails(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("integrity_check", CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    row = {
        "id": "q001",
        "question": "Why can't testing prove a system is defect-free?",
        "reference": "Testing shows defects that exist but cannot prove none remain.",
        "reference_pages": [17],
        "chapter": 1,
        "section": "1.3",
        "k_level": "K2",
        "type": "in_scope",
        "multi_chunk": False,
        "source": "llm",
        "pilot": True,
        "reviewed": True,  # reviewed, but no reviewed_by
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "golden_dataset.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    problems: list[str] = []
    module.check_golden_dataset(problems)
    assert problems, "a reviewed row with no reviewed_by must be reported"
    assert "reviewed_by" in problems[0]


# --- Nothing published may depend on answers.jsonl --------------------------


def test_tables_build_without_any_answers_file(tmp_path, monkeypatch):
    """Simulate a clean checkout: scores.csv present, answers.jsonl absent."""
    import shutil

    from istqb_rag.config import get_settings
    from istqb_rag.eval.readme_tables import build_tables, cells_with_missing_values

    runs = tmp_path / "runs"
    runs.mkdir()
    for run in (REPO_ROOT / "runs").iterdir():
        if not run.is_dir():
            continue
        target = runs / run.name
        target.mkdir()
        for name in ("config.json", "scores.csv", "summary.json"):
            if (run / name).exists():
                shutil.copy(run / name, target / name)  # deliberately not answers.jsonl

    assert not list(runs.rglob("answers.jsonl")), "the fixture must mimic a clean checkout"

    import dataclasses

    clean = dataclasses.replace(get_settings(), runs_dir=runs)
    tables = build_tables(clean)

    # every table builds, and none of them reports a hole
    assert set(tables) == {"baseline", "experiments", "variance"}
    assert cells_with_missing_values(tables) == []
    assert "0.778" in tables["experiments"]  # pilot-1 context recall
    assert "1.000 (n=8)" in tables["experiments"]  # pilot-1 citation validity, was the crash
    # the variance table needs answer hashes, which now come from scores.csv
    assert "identical answer every run" in tables["variance"]
    assert "4 of 15" in tables["variance"]


def test_missing_value_is_rendered_not_raised():
    from istqb_rag.eval.readme_tables import NA, _fmt

    assert _fmt(None) == NA
    assert _fmt(None, 11) == NA
    assert _fmt(0.7777) == "0.778"
    assert _fmt(0.7777, 11) == "0.778 (n=11)"


def test_a_missing_value_in_a_published_table_is_a_failure():
    from istqb_rag.eval.readme_tables import NA, cells_with_missing_values

    assert cells_with_missing_values({"experiments": f"| x | {NA} |"}) == ["experiments"]
    assert cells_with_missing_values({"experiments": "| x | 0.800 |"}) == []

"""Offline tests for golden dataset loading and validation."""

import json

import pytest

from istqb_rag.eval.dataset import DatasetError, load_golden


def _row(**overrides):
    base = {
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
        "reviewed": False,
    }
    base.update(overrides)
    return base


def _write(tmp_path, rows):
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_valid_file_loads(tmp_path):
    path = _write(
        tmp_path,
        [
            _row(),
            _row(
                id="q002",
                type="out_of_scope",
                reference=None,
                reference_pages=[],
                chapter=None,
                section=None,
                k_level=None,
            ),
            _row(id="q003", reviewed=True),
        ],
    )
    rows = load_golden(path, validate_mix=False)
    assert [r.id for r in rows] == ["q003"]  # unreviewed filtered by default
    all_rows = load_golden(path, validate_mix=False, include_unreviewed=True)
    assert len(all_rows) == 3


def test_duplicate_id_fails(tmp_path):
    path = _write(tmp_path, [_row(), _row(question="different wording")])
    with pytest.raises(DatasetError, match="duplicate id"):
        load_golden(path, validate_mix=False, include_unreviewed=True)


def test_bad_type_fails(tmp_path):
    path = _write(tmp_path, [_row(type="off_topic")])
    with pytest.raises(DatasetError, match="type"):
        load_golden(path, validate_mix=False, include_unreviewed=True)


def test_in_scope_missing_reference_fails(tmp_path):
    path = _write(tmp_path, [_row(reference="")])
    with pytest.raises(DatasetError, match="reference"):
        load_golden(path, validate_mix=False, include_unreviewed=True)


def test_in_scope_missing_pages_fails(tmp_path):
    path = _write(tmp_path, [_row(reference_pages=[])])
    with pytest.raises(DatasetError, match="reference_pages"):
        load_golden(path, validate_mix=False, include_unreviewed=True)


def test_non_in_scope_must_have_null_fields(tmp_path):
    path = _write(
        tmp_path,
        [
            _row(
                id="q009",
                type="out_of_scope",
                reference="not null",
                reference_pages=[],
                chapter=None,
                section=None,
                k_level=None,
            )
        ],
    )
    with pytest.raises(DatasetError, match="reference: null"):
        load_golden(path, validate_mix=False, include_unreviewed=True)


def test_limit_applies_after_review_filter(tmp_path):
    path = _write(tmp_path, [_row(reviewed=True), _row(id="q002", reviewed=True)])
    rows = load_golden(path, validate_mix=False, limit=1)
    assert [r.id for r in rows] == ["q001"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        load_golden(tmp_path / "nope.jsonl")


# --- Dataset mix (Part A) --------------------------------------------------


def _mix_rows():
    """A file matching the brief exactly: 60 in_scope, 5 not_in_syllabus, 10 out_of_scope."""
    per_chapter = {1: 10, 2: 8, 3: 6, 4: 18, 5: 14, 6: 4}
    rows, n = [], 0
    for chapter, count in per_chapter.items():
        for _ in range(count):
            n += 1
            rows.append(_row(id=f"q{n:03d}", chapter=chapter, section=f"{chapter}.1"))
    for row_type, count in (("not_in_syllabus", 5), ("out_of_scope", 10)):
        for _ in range(count):
            n += 1
            rows.append(
                _row(
                    id=f"q{n:03d}",
                    type=row_type,
                    reference=None,
                    reference_pages=[],
                    chapter=None,
                    section=None,
                    k_level=None,
                )
            )
    return rows


def test_correct_mix_passes(tmp_path):
    rows = load_golden(_write(tmp_path, _mix_rows()), include_unreviewed=True)
    assert len(rows) == 75


def test_wrong_type_count_fails(tmp_path):
    rows = [r for r in _mix_rows() if r["type"] != "out_of_scope"][:65]
    with pytest.raises(DatasetError, match="type out_of_scope: expected 10, found 0"):
        load_golden(_write(tmp_path, rows), include_unreviewed=True)


def test_wrong_chapter_count_fails(tmp_path):
    rows = _mix_rows()
    rows[0]["chapter"] = 2  # steal a row from chapter 1 and give it to chapter 2
    with pytest.raises(DatasetError, match="chapter 1: expected 10 in_scope, found 9"):
        load_golden(_write(tmp_path, rows), include_unreviewed=True)


def test_unexpected_chapter_fails(tmp_path):
    rows = _mix_rows()
    rows[0]["chapter"] = 7
    with pytest.raises(DatasetError, match="chapter 7: unexpected chapter"):
        load_golden(_write(tmp_path, rows), include_unreviewed=True)


def test_the_real_golden_file_matches_the_brief():
    """The committed dataset itself must satisfy the mix."""
    from pathlib import Path

    from istqb_rag.eval.dataset import dataset_counts

    path = Path(__file__).resolve().parents[1] / "data" / "golden.jsonl"
    rows = load_golden(path, include_unreviewed=True)
    types, chapters = dataset_counts(rows)
    assert len(rows) == 75
    assert types == {"in_scope": 60, "not_in_syllabus": 5, "out_of_scope": 10}
    assert chapters == {1: 10, 2: 8, 3: 6, 4: 18, 5: 14, 6: 4}
    assert all(r.source == "llm" for r in rows)
    assert sum(1 for r in rows if r.multi_chunk) >= 8

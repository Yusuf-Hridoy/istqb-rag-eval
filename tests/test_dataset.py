"""Offline tests for golden dataset loading and validation."""

import json

import pytest

from istqb_rag.eval.dataset import DatasetError, dataset_counts, load_golden


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
        "pilot": False,
        "reviewed": False,
    }
    base.update(overrides)
    return base


def _write(tmp_path, rows):
    path = tmp_path / "golden_dataset.jsonl"
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
            _row(id="q003", reviewed=True, reviewed_by="human"),
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
    path = _write(
        tmp_path,
        [
            _row(reviewed=True, reviewed_by="llm"),
            _row(id="q002", reviewed=True, reviewed_by="llm"),
        ],
    )
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

    path = Path(__file__).resolve().parents[1] / "data" / "golden_dataset.jsonl"
    rows = load_golden(path, include_unreviewed=True)
    types, chapters = dataset_counts(rows)
    assert len(rows) == 75
    assert types == {"in_scope": 60, "not_in_syllabus": 5, "out_of_scope": 10}
    assert chapters == {1: 10, 2: 8, 3: 6, 4: 18, 5: 14, 6: 4}
    assert all(r.source == "llm" for r in rows)
    assert sum(1 for r in rows if r.multi_chunk) >= 8


# --- Pilot subset and human-authored rows (amendment) ----------------------


def test_human_rows_do_not_count_towards_the_mix(tmp_path):
    """A user-added source: "human" row must not break the 60/5/10 check."""
    rows = _mix_rows()
    rows.append(_row(id="q900", source="human", chapter=4, section="4.2"))
    loaded = load_golden(_write(tmp_path, rows), include_unreviewed=True)
    assert len(loaded) == 76  # the human row loads
    types, chapters = dataset_counts(loaded)
    assert types["in_scope"] == 60  # but is not counted in the mix
    assert chapters[4] == 18


def test_human_rows_still_validated_per_row(tmp_path):
    bad = _row(id="q900", source="human", reference=None)
    with pytest.raises(DatasetError, match="in_scope row needs a non-empty reference"):
        load_golden(_write(tmp_path, _mix_rows() + [bad]), include_unreviewed=True)


def test_pilot_must_be_a_boolean(tmp_path):
    rows = _mix_rows()
    rows[0]["pilot"] = "yes"
    with pytest.raises(DatasetError, match="pilot must be a boolean"):
        load_golden(_write(tmp_path, rows), include_unreviewed=True)


def test_the_real_golden_file_has_the_agreed_pilot_subset():
    """The committed dataset's 15 pilot rows match the shape the user asked for."""
    from collections import Counter
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data" / "golden_dataset.jsonl"
    rows = load_golden(path, include_unreviewed=True)
    pilot = [r for r in rows if r.pilot]
    assert len(pilot) == 15
    assert Counter(r.type for r in pilot) == {
        "in_scope": 11,
        "not_in_syllabus": 2,
        "out_of_scope": 2,
    }
    in_scope = [r for r in pilot if r.type == "in_scope"]
    assert Counter(r.chapter for r in in_scope) == {1: 2, 2: 2, 3: 1, 4: 3, 5: 2, 6: 1}
    assert Counter(r.k_level for r in in_scope) == {"K1": 4, "K2": 5, "K3": 2}
    assert sum(1 for r in pilot if r.multi_chunk) >= 3
    assert any("seven testing principles" in r.question.lower() for r in pilot)
    # the 15 pilot rows have been verified; the other 60 are untouched
    assert all(r.reviewed and r.reviewed_by == "llm" for r in pilot)
    assert all(not r.reviewed for r in rows if not r.pilot)


def test_reviewed_row_must_name_its_reviewer(tmp_path):
    rows = [_row(id="q001", reviewed=True)]  # no reviewed_by
    with pytest.raises(DatasetError, match="a reviewed row needs reviewed_by"):
        load_golden(_write(tmp_path, rows), validate_mix=False, include_unreviewed=True)


def test_unknown_reviewer_rejected(tmp_path):
    rows = [_row(id="q001", reviewed=True, reviewed_by="nobody")]
    with pytest.raises(DatasetError, match="reviewed_by must be one of"):
        load_golden(_write(tmp_path, rows), validate_mix=False, include_unreviewed=True)

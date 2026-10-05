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
    rows = load_golden(path)
    assert [r.id for r in rows] == ["q003"]  # unreviewed filtered by default
    all_rows = load_golden(path, include_unreviewed=True)
    assert len(all_rows) == 3


def test_duplicate_id_fails(tmp_path):
    path = _write(tmp_path, [_row(), _row(question="different wording")])
    with pytest.raises(DatasetError, match="duplicate id"):
        load_golden(path, include_unreviewed=True)


def test_bad_type_fails(tmp_path):
    path = _write(tmp_path, [_row(type="off_topic")])
    with pytest.raises(DatasetError, match="type"):
        load_golden(path, include_unreviewed=True)


def test_in_scope_missing_reference_fails(tmp_path):
    path = _write(tmp_path, [_row(reference="")])
    with pytest.raises(DatasetError, match="reference"):
        load_golden(path, include_unreviewed=True)


def test_in_scope_missing_pages_fails(tmp_path):
    path = _write(tmp_path, [_row(reference_pages=[])])
    with pytest.raises(DatasetError, match="reference_pages"):
        load_golden(path, include_unreviewed=True)


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
        load_golden(path, include_unreviewed=True)


def test_limit_applies_after_review_filter(tmp_path):
    path = _write(tmp_path, [_row(reviewed=True), _row(id="q002", reviewed=True)])
    rows = load_golden(path, limit=1)
    assert [r.id for r in rows] == ["q001"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        load_golden(tmp_path / "nope.jsonl")

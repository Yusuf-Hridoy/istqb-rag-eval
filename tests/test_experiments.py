"""Offline tests for the Phase 3 additions: chunking, structured answers, metrics, compare."""

import dataclasses
import math

import pytest
from langchain_core.documents import Document

from istqb_rag.config import active_collection
from istqb_rag.eval.compare_runs import compare
from istqb_rag.eval.deterministic_metrics import (
    citation_rate,
    citation_validity,
    cohens_kappa,
    confusion_matrix,
    page_hit_rate,
)
from istqb_rag.eval.step2_judge_scores import metrics_for
from istqb_rag.ingest import find_headings, is_section_number_line, split_pages, split_sections
from istqb_rag.structured_answer import parse_structured_reply
from tests.conftest import make_settings

# --- Defaults must reproduce Phase 2 ---------------------------------------


def test_page_chunking_is_still_the_default():
    """Chunking was not changed by Phase 4: section mode lost its experiment."""
    from istqb_rag.config import _str

    assert _str("CHUNKING", "page") in ("page", "section")
    settings = make_settings()
    assert settings.chunking == "page"
    assert active_collection(settings) == settings.collection_name


def test_text_mode_still_reproduces_the_pilot_1_baseline():
    """The default moved to structured in Phase 4; text must stay available."""
    import dataclasses

    text_mode = dataclasses.replace(make_settings(), answer_format="text")
    assert text_mode.answer_format == "text"
    assert active_collection(text_mode) == text_mode.collection_name


def test_section_mode_uses_its_own_collection():
    """The baseline collection must never be touched by the experiment."""
    settings = make_settings()
    section = dataclasses.replace(settings, chunking="section")
    assert active_collection(section) == f"{settings.collection_name}_section"
    assert active_collection(section) != active_collection(settings)


# --- Section chunking -------------------------------------------------------

FAKE_PAGES = [
    Document(
        page_content=(
            "1.1  What is Testing?\n"
            "Testing assesses quality.\n"
            "It is not only execution.\n"
            "1.1.1.\n"
            "Test Objectives\n"
            "Evaluating work products.\n"
            "Causing failures.\n"
        ),
        metadata={"page": 15},
    ),
    Document(
        page_content="1.2  Why is Testing Necessary?\nTesting reduces risk.\n",
        metadata={"page": 16},
    ),
]


def test_headings_detected_in_both_shapes():
    """Inline headings and number-on-its-own-line headings both count."""
    lines = [line for page in FAKE_PAGES for line in page.page_content.splitlines()]
    found = find_headings(lines)
    assert [section_id for _, section_id, _ in found] == ["1.1", "1.1.1", "1.2"]


def test_bare_section_numbers_are_recognised_for_protection():
    assert is_section_number_line("5.1.1.")
    assert is_section_number_line("  6.2 ")
    assert not is_section_number_line("5.1. Test Planning")
    assert not is_section_number_line("Page 17 of 78")


def test_no_chunk_spans_two_sections():
    settings = make_settings(chunk_size=1000, chunk_overlap=0)
    chunks = split_sections(FAKE_PAGES, settings)
    assert len(chunks) == 3
    # each chunk carries exactly one heading, and no text from a sibling section
    by_section = {c.metadata["section_id"]: c.page_content for c in chunks}
    assert by_section["1.1"].startswith("1.1 What is Testing?")
    assert "Test Objectives" not in by_section["1.1"]  # 1.1.1 did not bleed in
    assert by_section["1.1.1"].startswith("1.1.1 Test Objectives")
    assert "Testing assesses quality" not in by_section["1.1.1"]
    assert by_section["1.2"].startswith("1.2 Why is Testing Necessary?")
    assert {c.metadata["section_id"] for c in chunks} == {"1.1", "1.1.1", "1.2"}


def test_section_metadata_keeps_start_page_and_adds_section_id():
    chunks = split_sections(FAKE_PAGES, make_settings())
    first = next(c for c in chunks if c.metadata["section_id"] == "1.1")
    assert first.metadata["page"] == 15  # citations keep working
    assert first.metadata["chunk_id"].startswith("s1.1-")


def test_long_section_splits_and_every_piece_keeps_the_heading():
    long_page = [
        Document(
            page_content="2.1  Long Section\n" + ("sentence about testing. " * 120),
            metadata={"page": 20},
        )
    ]
    settings = make_settings(chunk_size=200, chunk_overlap=0)
    chunks = split_sections(long_page, settings)
    assert len(chunks) > 1
    assert all(c.page_content.startswith("2.1 Long Section") for c in chunks)
    assert all(c.metadata["section_id"] == "2.1" for c in chunks)
    assert len({c.metadata["chunk_id"] for c in chunks}) == len(chunks)  # ids unique


def test_duplicate_section_numbers_keep_only_the_longest_body():
    """A contents page lists the same numbers; the real section is the longer one."""
    pages = [
        Document(page_content="3.1  Static Testing Basics\n", metadata={"page": 30}),
        Document(
            page_content="3.1  Static Testing Basics\n" + ("real body text. " * 20),
            metadata={"page": 32},
        ),
    ]
    chunks = split_sections(pages, make_settings())
    assert len({c.metadata["chunk_id"] for c in chunks}) == len(chunks)
    assert {c.metadata["page"] for c in chunks} == {32}


def test_page_mode_output_is_unchanged_by_the_new_switch():
    """The Phase 2 path must be byte-identical whatever the switch defaults to."""
    settings = make_settings()
    page_chunks = split_pages(FAKE_PAGES, settings)
    again = split_pages(FAKE_PAGES, dataclasses.replace(settings, chunking="page"))
    assert [c.page_content for c in page_chunks] == [c.page_content for c in again]
    assert all("section_id" not in c.metadata for c in page_chunks)


# --- Structured answers -----------------------------------------------------


def test_valid_json_maps_to_status_and_citations():
    reply = parse_structured_reply(
        '{"status": "answered", "answer": "Testing finds defects.", "cited_pages": [15, 17]}'
    )
    assert reply.status == "answered"
    assert reply.answer == "Testing finds defects."
    assert reply.cited_pages == [15, 17]
    assert not reply.missing_citation


@pytest.mark.parametrize(
    "model_status,expected",
    [("answered", "answered"), ("refused", "refused"), ("not_found", "no_context")],
)
def test_each_status_word_maps_to_the_pipeline_status(model_status, expected):
    raw = f'{{"status": "{model_status}", "answer": "x", "cited_pages": [15]}}'
    assert parse_structured_reply(raw).status == expected


def test_answered_with_no_citation_is_flagged():
    reply = parse_structured_reply('{"status": "answered", "answer": "x", "cited_pages": []}')
    assert reply.status == "answered"
    assert reply.missing_citation is True


def test_refused_with_no_citation_is_not_flagged():
    reply = parse_structured_reply('{"status": "refused", "answer": "no", "cited_pages": []}')
    assert reply.missing_citation is False


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "",
        '{"status": "maybe", "answer": "x", "cited_pages": []}',  # unknown status
        '{"answer": "x"}',  # no status
        '{"status": "answered", "answer": 42, "cited_pages": []}',  # answer not a string
        "[1, 2, 3]",  # not an object
    ],
)
def test_unusable_json_returns_none_so_the_caller_falls_back(raw):
    assert parse_structured_reply(raw) is None


def test_page_numbers_are_cleaned():
    reply = parse_structured_reply(
        '{"status": "answered", "answer": "x", "cited_pages": [15, "17", -2, 15, true, "p"]}'
    )
    assert reply.cited_pages == [15, 17]


# --- Deterministic metrics --------------------------------------------------


def _row(row_id, **kw):
    base = {
        "id": row_id,
        "type": "in_scope",
        "status": "answered",
        "cited_pages": "15",
        "retrieved_pages": "15;17",
    }
    base.update(kw)
    return base


def test_citation_rate_counts_only_answered_in_scope_rows():
    rows = [
        _row("q1", cited_pages="15"),
        _row("q2", cited_pages=""),
        _row("q3", status="no_context", cited_pages=""),
        _row("q4", type="out_of_scope", status="refused", cited_pages=""),
    ]
    rate = citation_rate(rows)
    assert rate["answered"] == 2 and rate["cited"] == 1
    assert rate["rate"] == 0.5
    assert rate["uncited_ids"] == ["q2"]


def test_citation_rate_works_on_an_old_scores_csv_without_new_columns():
    """pilot-1 predates retrieved_pages; cited_pages has always been there."""
    rows = [{"id": "q1", "type": "in_scope", "status": "answered", "cited_pages": "15;17"}]
    assert citation_rate(rows)["rate"] == 1.0


def test_page_hit_rate_counts_a_row_as_hit_when_any_page_overlaps():
    rows = [
        _row("q1", retrieved_pages="15;99"),  # 15 is a reference page
        _row("q2", retrieved_pages="98;99"),  # miss
        _row("q3", status="error", retrieved_pages="15"),  # excluded
    ]
    hit = page_hit_rate(rows, {"q1": [15], "q2": [17], "q3": [15]})
    assert hit["hits"] == 1 and hit["total"] == 2
    assert hit["rate"] == 0.5
    assert hit["missed_ids"] == ["q2"]


def test_citation_validity_flags_invented_pages():
    """One cited page was retrieved, one was invented → 0.5."""
    rows = [_row("q1", cited_pages="15;99", retrieved_pages="15;17")]
    result = citation_validity(rows)
    assert result["rate"] == 0.5
    assert result["valid_pages"] == 1
    assert result["total_pages"] == 2
    assert result["invalid_ids"] == ["q1"]


def test_citation_validity_skips_rows_with_nothing_cited():
    rows = [
        _row("q1", cited_pages="15", retrieved_pages="15;17"),
        _row("q2", cited_pages=""),
        _row("q3", status="no_context", cited_pages=""),
        _row("q4", cited_pages="17", retrieved_pages="15"),  # fully invented
    ]
    result = citation_validity(rows)
    assert result["rows"] == 2
    assert result["rate"] == 0.5  # macro average of per-row rates: (1.0 + 0.0) / 2
    assert result["invalid_ids"] == ["q4"]


# --- Cheap scoring ----------------------------------------------------------


def test_only_metrics_narrows_what_the_judge_scores():
    assert metrics_for("in_scope", "answered", only=["context_recall"]) == ["context_recall"]
    assert metrics_for("in_scope", "no_context", only=["context_recall"]) == ["context_recall"]
    # a metric the routing table would not have given this row is not added back
    assert metrics_for("in_scope", "no_context", only=["faithfulness"]) == []
    assert metrics_for("out_of_scope", "refused", only=["context_recall"]) == []
    # unrestricted behaviour is unchanged
    assert len(metrics_for("in_scope", "answered")) == 4


# --- Cohen's kappa ----------------------------------------------------------


def test_kappa_perfect_agreement():
    pairs = [("yes", "yes"), ("no", "no"), ("yes", "yes"), ("no", "no")]
    result = cohens_kappa(pairs)
    assert result["percent_agreement"] == 1.0
    assert result["kappa"] == 1.0


def test_kappa_chance_level_agreement_is_zero():
    # po = 0.5, and each rater says yes half the time, so pe = 0.5 too
    pairs = [("yes", "yes"), ("yes", "no"), ("no", "yes"), ("no", "no")]
    result = cohens_kappa(pairs)
    assert result["percent_agreement"] == 0.5
    assert result["kappa"] == 0.0


def test_kappa_hand_worked_example():
    # 10 items: both yes 6, both no 2, human yes/judge no 1, human no/judge yes 1
    pairs = [("yes", "yes")] * 6 + [("no", "no")] * 2 + [("yes", "no")] * 1 + [("no", "yes")] * 1
    result = cohens_kappa(pairs)
    assert result["percent_agreement"] == 0.8
    # pe = 0.7*0.7 + 0.3*0.3 = 0.58 ; kappa = (0.8-0.58)/(1-0.58) = 0.5238
    assert result["expected_agreement"] == 0.58
    assert result["kappa"] == pytest.approx(0.5238, abs=1e-4)


def test_kappa_undefined_when_every_label_is_identical():
    """pe = 1, so the formula would divide by zero. Undefined, not 0 or 1."""
    result = cohens_kappa([("yes", "yes")] * 5)
    assert result["percent_agreement"] == 1.0
    assert result["kappa"] is None
    assert "undefined" in result["note"]


def test_kappa_with_no_rows():
    assert cohens_kappa([])["kappa"] is None


def test_confusion_matrix_counts_each_cell():
    pairs = [("yes", "yes"), ("yes", "no"), ("no", "yes"), ("no", "no"), ("yes", "yes")]
    assert confusion_matrix(pairs) == {
        "both_yes": 2,
        "both_no": 1,
        "human_yes_judge_no": 1,
        "human_no_judge_yes": 1,
    }


# --- Compare runs -----------------------------------------------------------


def _scores_row(row_id, status="answered", cp=0.8, cr=0.8, multi=False, **kw):
    row = {
        "id": row_id,
        "type": "in_scope",
        "status": status,
        "multi_chunk": multi,
        "cited_pages": "15",
        "retrieved_pages": "15",
        "chapter": "1",
        "k_level": "K2",
        "latency_ms": 100,
        "judge_truncated": False,
        "context_precision": cp,
        "context_recall": cr,
        "faithfulness": math.nan,
        "response_relevancy": math.nan,
    }
    row.update(kw)
    return row


def test_compare_joins_on_id_not_order():
    base = [_scores_row("q1", cr=0.2), _scores_row("q2", cr=0.9)]
    new = [_scores_row("q2", cr=0.9), _scores_row("q1", cr=0.9)]  # reversed
    data = compare(base, new)
    assert data["rows_compared"] == 2
    assert data["metrics"]["context_recall"]["base_mean"] == 0.55
    assert data["metrics"]["context_recall"]["new_mean"] == 0.9
    assert data["metrics"]["context_recall"]["delta"] == 0.35


def test_compare_lists_only_rows_that_moved_or_changed_status():
    base = [_scores_row("q1", cr=0.2), _scores_row("q2", cr=0.80), _scores_row("q3")]
    new = [
        _scores_row("q1", cr=0.9),  # moved 0.7
        _scores_row("q2", cr=0.85),  # moved 0.05, under the threshold
        _scores_row("q3", status="no_context"),  # status change only
    ]
    changed = {r["id"]: r for r in compare(base, new)["changed_rows"]}
    assert set(changed) == {"q1", "q3"}
    assert changed["q1"]["moved"]["context_recall"]["delta"] == 0.7
    assert changed["q3"]["status_changed"] is True


def test_compare_counts_direction_per_metric():
    base = [_scores_row("q1", cr=0.2), _scores_row("q2", cr=0.9), _scores_row("q3", cr=0.5)]
    new = [_scores_row("q1", cr=0.9), _scores_row("q2", cr=0.1), _scores_row("q3", cr=0.5)]
    direction = compare(base, new)["direction"]["context_recall"]
    assert direction == {"higher": 1, "lower": 1, "unchanged": 1}


def test_compare_reports_rows_present_in_only_one_run():
    data = compare([_scores_row("q1")], [_scores_row("q1"), _scores_row("q2")])
    assert data["new_only_ids"] == ["q2"]
    assert data["base_only_ids"] == []


# --- Citation validity ------------------------------------------------------


def test_citation_validity_all_pages_retrieved():
    rows = [_row("q1", cited_pages="15;17", retrieved_pages="15;17;20")]
    validity = citation_validity(rows)
    assert validity["rate"] == 1.0
    assert validity["invalid_ids"] == []


def test_citation_validity_averages_per_row_not_per_page():
    """A row citing many pages must not outvote a row citing one."""
    rows = [
        _row("q1", cited_pages="15;16;17;18", retrieved_pages="15;16;17;18"),  # 1.0
        _row("q2", cited_pages="99", retrieved_pages="15"),  # 0.0
    ]
    validity = citation_validity(rows)
    assert validity["rate"] == 0.5  # macro: (1.0 + 0.0) / 2
    assert validity["valid_pages"] == 4 and validity["total_pages"] == 5  # micro differs


def test_citation_validity_with_no_cited_rows_at_all():
    assert citation_validity([_row("q1", cited_pages="")])["rate"] is None


# --- Unmeasured metrics vs failed metrics -----------------------------------


def test_unmeasured_metric_is_null_with_n_zero():
    """A metric the run never requested is absent, not a column of failures."""
    from istqb_rag.eval.step3_build_summary import build_summary

    rows = [_scores_row("q1", cr=0.8), _scores_row("q2", cr=0.6)]
    summary = build_summary(rows, measured=["context_recall"])
    recall = summary["overall"]["context_recall"]
    faith = summary["overall"]["faithfulness"]
    assert recall["mean"] == 0.7 and recall["scored"] == 2 and recall["unmeasured"] is False
    assert faith["mean"] is None and faith["scored"] == 0 and faith["expected"] == 0
    assert faith["unmeasured"] is True


def test_unmeasured_metric_never_invalidates_a_run():
    """Blank because unrequested must not read as 100% parse failure."""
    from istqb_rag.eval.step3_build_summary import build_summary, failed_nan_metrics

    rows = [_scores_row(f"q{i}", cr=0.8) for i in range(5)]
    summary = build_summary(rows, measured=["context_recall"])
    assert summary["overall"]["faithfulness"]["parse_failure_rate"] == 0.0
    assert failed_nan_metrics(summary) == []


def test_measured_none_keeps_the_old_behaviour():
    """With no metrics_scored recorded, every metric is still treated as judged."""
    from istqb_rag.eval.step3_build_summary import build_summary

    rows = [_scores_row("q1", cr=0.8)]
    summary = build_summary(rows)
    assert summary["overall"]["context_recall"]["unmeasured"] is False
    assert summary["overall"]["faithfulness"]["expected"] == 1  # still expected, still missing


# --- Answer variance study --------------------------------------------------


def _sample(run_id, statuses, hashes, fallbacks=0, cited=None):
    from istqb_rag.eval.answer_variance import RunSample

    rows = []
    for row_id, (row_type, status) in statuses.items():
        rows.append(
            {
                "id": row_id,
                "type": row_type,
                "status": status,
                "cited_pages": (cited or {}).get(row_id, "15" if status == "answered" else ""),
                "retrieved_pages": "15",
            }
        )
    return RunSample(run_id=run_id, rows=rows, answer_hashes=hashes, fallbacks=fallbacks)


def test_spread_reports_mean_min_max():
    from istqb_rag.eval.answer_variance import spread

    assert spread([0.8, 1.0, 0.9, 1.0]) == {"mean": 0.925, "min": 0.8, "max": 1.0, "runs": 4}
    assert spread([None, 0.5]) == {"mean": 0.5, "min": 0.5, "max": 0.5, "runs": 1}
    assert spread([None, None])["mean"] is None


def test_status_flips_counted_per_row():
    from istqb_rag.eval.answer_variance import mode_summary

    base = {"q1": ("in_scope", "answered"), "q2": ("out_of_scope", "refused")}
    flipped = {"q1": ("in_scope", "answered"), "q2": ("out_of_scope", "answered")}
    summary = mode_summary(
        [
            _sample("r1", base, {"q1": "a", "q2": "b"}),
            _sample("r2", flipped, {"q1": "a", "q2": "c"}),
        ]
    )
    assert set(summary["status_flips"]) == {"q2"}
    assert summary["status_flips"]["q2"] == ["answered", "refused"]


def test_answer_stability_from_hashes():
    from istqb_rag.eval.answer_variance import mode_summary

    rows = {"q1": ("in_scope", "answered"), "q2": ("in_scope", "answered")}
    summary = mode_summary(
        [
            _sample("r1", rows, {"q1": "same", "q2": "x"}),
            _sample("r2", rows, {"q1": "same", "q2": "y"}),
        ]
    )
    assert summary["answer_variants"] == {"q1": 1, "q2": 2}
    assert summary["identical_rows"] == 1 and summary["measured_rows"] == 2
    assert summary["stability_rate"] == 0.5


def _modes(struct_fallbacks=0, struct_scope_answered=False, struct_cite="15", text_cite="15"):
    from istqb_rag.eval.answer_variance import mode_summary

    rows = {"q1": ("in_scope", "answered"), "q2": ("out_of_scope", "refused")}
    struct_rows = dict(rows)
    if struct_scope_answered:
        struct_rows["q2"] = ("out_of_scope", "answered")
    text = mode_summary([_sample("t1", rows, {"q1": "a"}, cited={"q1": text_cite})])
    structured = mode_summary(
        [
            _sample(
                "s1",
                struct_rows,
                {"q1": "b"},
                fallbacks=struct_fallbacks,
                cited={"q1": struct_cite},
            )
        ]
    )
    return text, structured


def test_decision_rule_all_conditions_met():
    from istqb_rag.eval.answer_variance import apply_decision_rule

    decision = apply_decision_rule(*_modes())
    assert decision["default"] == "structured"
    assert decision["failed"] == []


def test_decision_rule_fails_on_too_many_fallbacks():
    from istqb_rag.eval.answer_variance import apply_decision_rule

    decision = apply_decision_rule(*_modes(struct_fallbacks=2))
    assert decision["default"] == "text" and decision["failed"] == ["a"]


def test_decision_rule_fails_when_a_scope_row_is_answered():
    from istqb_rag.eval.answer_variance import apply_decision_rule

    decision = apply_decision_rule(*_modes(struct_scope_answered=True))
    assert decision["default"] == "text" and decision["failed"] == ["b"]


def test_decision_rule_fails_on_lower_citation_rate():
    from istqb_rag.eval.answer_variance import apply_decision_rule

    # structured cites nothing, text cites a page: structured mean is lower
    decision = apply_decision_rule(*_modes(struct_cite=""))
    assert decision["default"] == "text" and decision["failed"] == ["c"]


def test_decision_rule_allows_exactly_one_fallback():
    """The rule says 'at most 1', so one must pass."""
    from istqb_rag.eval.answer_variance import apply_decision_rule

    assert apply_decision_rule(*_modes(struct_fallbacks=1))["default"] == "structured"


def test_answer_hashes_come_from_scores_not_answers_file():
    """Stability must be measurable on a clean checkout."""
    from istqb_rag.eval.answer_variance import answer_hashes

    rows = [
        {"id": "q1", "answer_sha256": "abc", "format_fallback": ""},
        {"id": "q2", "answer_sha256": "def", "format_fallback": "true"},
        {"id": "q3", "answer_sha256": "", "format_fallback": ""},  # predates the column
    ]
    hashes, fallbacks = answer_hashes(rows)
    assert hashes == {"q1": "abc", "q2": "def"}  # q3 omitted, not counted as identical
    assert fallbacks == 1

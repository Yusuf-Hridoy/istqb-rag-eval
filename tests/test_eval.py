"""Offline tests for eval scoring, routing, resume and reporting."""

import csv
import math

import pytest

from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.eval.step1_ask_questions import run_generate
from istqb_rag.eval.step2_judge_scores import (
    METRIC_KEYS,
    SCORES_COLUMNS,
    JudgeQuotaExhausted,
    ScoreOutcome,
    is_daily_quota_error,
    is_quota_error,
    is_request_too_large,
    metrics_for,
    retry_after_seconds,
    run_score,
    scope_verdict,
)
from istqb_rag.eval.step3_build_summary import (
    MIN_GROUP_N,
    NAN_RATE_LIMIT,
    RunInvalid,
    build_summary,
    failed_nan_metrics,
    run_report,
)
from istqb_rag.result_types import RagResult, RetrievedChunk
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
        pilot=False,
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
        return ScoreOutcome(values={k: 0.5 for k in metric_keys}, calls=len(metric_keys))

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
    truncated=False,
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
        "judge_truncated": truncated,
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
    # expected = the 3 in_scope non-error rows; only q003's NaN is a real failure
    assert overall["expected"] == 3
    assert overall["nan"] == 1

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


def _read_scores(settings, run_id):
    with (settings.runs_dir / run_id / "scores.csv").open() as f:
        return list(csv.DictReader(f))


def _write_scores(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCORES_COLUMNS)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["multi_chunk"] = str(out["multi_chunk"]).lower()
            for key in METRIC_KEYS:
                out[key] = "" if math.isnan(out[key]) else f"{out[key]:.4f}"
            writer.writerow(out)


# --- Quota handling, resume correctness and the NaN guard (Part B) ---------


class _QuotaError(Exception):
    """Stands in for GoogleRateLimitError / groq.RateLimitError."""

    def __init__(self):
        super().__init__("429 RESOURCE_EXHAUSTED: quota exceeded for this model")


def test_is_quota_error_recognises_provider_wording():
    assert is_quota_error(_QuotaError())
    assert is_quota_error(Exception("Error code: 429 - rate limit reached"))
    assert not is_quota_error(ValueError("could not parse judge output"))


def test_quota_error_stops_scoring_and_saves_nothing(tmp_path):
    """A quota failure stops the stage; the failing row is never written."""
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002"), _row("q003")]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    calls = []

    def quota_scorer(question, response, reference, contexts, metric_keys):
        calls.append(question)
        if len(calls) == 1:
            return ScoreOutcome(values={k: 0.5 for k in metric_keys})
        return ScoreOutcome(api_error="429 RESOURCE_EXHAUSTED", quota_exhausted=True)

    with pytest.raises(JudgeQuotaExhausted):
        run_score("test-run", rows, quota_scorer, settings=settings)

    saved = _read_scores(settings, "test-run")
    assert [r["id"] for r in saved] == ["q001"]  # q002 not written, q003 never attempted
    assert len(calls) == 2  # stopped immediately, did not go on to q003


def test_rerun_after_quota_resumes_from_the_unscored_row(tmp_path):
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002"), _row("q003")]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    def quota_on_second(question, response, reference, contexts, metric_keys):
        if "q001" not in question:
            return ScoreOutcome(api_error="429", quota_exhausted=True)
        return ScoreOutcome(values={k: 0.5 for k in metric_keys})

    with pytest.raises(JudgeQuotaExhausted):
        run_score("test-run", rows, quota_on_second, settings=settings)

    seen = []

    def good_scorer(question, response, reference, contexts, metric_keys):
        seen.append(question)
        return ScoreOutcome(values={k: 0.9 for k in metric_keys})

    stats = run_score("test-run", rows, good_scorer, settings=settings)
    assert stats["skipped"] == 1  # q001 already saved
    assert len(seen) == 2  # only q002 and q003 were re-judged
    assert [r["id"] for r in _read_scores(settings, "test-run")] == ["q001", "q002", "q003"]


def test_api_error_nan_is_not_saved_but_parse_failure_nan_is(tmp_path):
    """The two kinds of NaN are treated differently (brief: resume correctness)."""
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002")]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    def mixed_scorer(question, response, reference, contexts, metric_keys):
        if "q001" in question:
            # Judge answered but Ragas could not read it: a real measurement.
            return ScoreOutcome(values={k: math.nan for k in metric_keys})
        # Judge never answered: not a measurement at all.
        return ScoreOutcome(api_error="ConnectionError: judge unreachable")

    stats = run_score("test-run", rows, mixed_scorer, settings=settings)
    assert stats["api_errors"] == 1
    saved = _read_scores(settings, "test-run")
    assert [r["id"] for r in saved] == ["q001"]
    assert saved[0]["context_precision"] == ""  # parse-failure NaN written as empty


def test_nan_rate_counts_only_rows_the_routing_table_expected():
    """An out_of_scope row has no faithfulness; that is not a parse failure."""
    rows = [
        _score_row("q001", cp=0.8, cr=0.8, f=0.8, rr=0.8),
        _score_row(
            "q002",
            row_type="out_of_scope",
            status="refused",
            cp=math.nan,
            cr=math.nan,
            f=math.nan,
            rr=math.nan,
        ),
    ]
    stats = build_summary(rows)["overall"]["faithfulness"]
    assert stats["expected"] == 1 and stats["nan"] == 0 and stats["nan_rate"] == 0.0


def test_report_refuses_to_write_summary_when_nans_exceed_10_percent(tmp_path):
    settings = make_settings(runs_dir=tmp_path / "runs")
    run_dir = settings.runs_dir / "bad-run"
    run_dir.mkdir(parents=True)
    # 10 in-scope answered rows, 3 with an unparseable faithfulness verdict.
    rows = [_score_row(f"q{i:03d}", f=(math.nan if i < 3 else 0.9)) for i in range(10)]
    _write_scores(run_dir / "scores.csv", rows)

    with pytest.raises(RunInvalid, match="faithfulness"):
        run_report("bad-run", settings=settings)
    assert not (run_dir / "summary.json").exists()


def test_report_writes_summary_when_nans_are_within_budget(tmp_path):
    settings = make_settings(runs_dir=tmp_path / "runs")
    run_dir = settings.runs_dir / "ok-run"
    run_dir.mkdir(parents=True)
    rows = [_score_row(f"q{i:03d}", f=(math.nan if i == 0 else 0.9)) for i in range(20)]
    _write_scores(run_dir / "scores.csv", rows)

    summary = run_report("ok-run", settings=settings)
    assert (run_dir / "summary.json").exists()
    assert summary["overall"]["faithfulness"]["nan"] == 1
    assert failed_nan_metrics(summary) == []


GROQ_OTPM = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`qwen/qwen3.8-27b` on output tokens per minute (OTPM): Limit 1000, Used 338, "
    "Requested 674. Please try again in 720ms."
)
GEMINI_DAILY = (
    "429 RESOURCE_EXHAUSTED quota exceeded for generate_content_free_tier_requests, "
    "limit: 20 ... 'retryDelay': '45700s'"
)


@pytest.mark.parametrize(
    "text,expected",
    [
        (GROQ_OTPM, 0.72),  # the trailing full stop must not read as minutes
        ("Please try again in 1m30s.", 90.0),
        ("Please try again in 2h5m.", 7500.0),
        (GEMINI_DAILY, 45700.0),
        ("no wait mentioned", None),
    ],
)
def test_retry_after_seconds_parses_provider_wording(text, expected):
    assert retry_after_seconds(Exception(text)) == expected


def test_per_minute_limit_is_not_treated_as_daily_exhaustion():
    """A 720ms token-bucket wait must be retried, not stop the whole run."""
    assert not is_daily_quota_error(Exception(GROQ_OTPM))
    assert is_daily_quota_error(Exception(GEMINI_DAILY))
    assert is_daily_quota_error(Exception("429: limit 1000 requests per day"))
    assert not is_daily_quota_error(ValueError("could not parse judge output"))


# --- Group n and the small-sample flag (pilot amendment) -------------------


def test_summary_carries_n_per_group():
    rows = [
        _score_row("q001", chapter="1"),
        _score_row("q002", chapter="1"),
        _score_row("q003", chapter="1"),
        _score_row("q004", chapter="2"),
    ]
    summary = build_summary(rows)
    assert summary["by_chapter"]["1"]["n"] == 3
    assert summary["by_chapter"]["2"]["n"] == 1


def test_groups_below_min_n_are_flagged_but_still_reported():
    """A thin group keeps its mean — it is marked, not dropped."""
    rows = [_score_row("q001", chapter="1", cp=0.8), _score_row("q002", chapter="2", cp=0.4)]
    summary = build_summary(rows)
    assert summary["by_chapter"]["1"]["n_too_small"] is True
    assert summary["by_chapter"]["1"]["context_precision"]["mean"] == 0.8


def test_group_at_min_n_is_not_flagged():
    rows = [_score_row(f"q{i:03d}", chapter="1") for i in range(MIN_GROUP_N)]
    summary = build_summary(rows)
    assert summary["by_chapter"]["1"]["n"] == MIN_GROUP_N
    assert summary["by_chapter"]["1"]["n_too_small"] is False


def test_n_does_not_leak_into_overall_stats():
    """failed_nan_metrics iterates overall; a stray int there would crash it."""
    summary = build_summary([_score_row("q001")])
    assert set(summary["overall"]) == set(METRIC_KEYS)
    assert failed_nan_metrics(summary) == []


def test_quota_stop_still_scores_rows_that_need_no_judge(tmp_path):
    """Scope rows are decided by the routing table, so judge quota cannot block them."""
    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [
        _row("q001"),
        _row("q002"),  # in_scope: needs the judge, hits the quota
        _row("q003", row_type="out_of_scope"),
        _row("q004", row_type="not_in_syllabus"),
    ]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    def quota_after_first(question, response, reference, contexts, metric_keys):
        if "q001" in question:
            return ScoreOutcome(values={k: 0.5 for k in metric_keys})
        return ScoreOutcome(api_error="429 tokens per day", quota_exhausted=True)

    with pytest.raises(JudgeQuotaExhausted):
        run_score("test-run", rows, quota_after_first, settings=settings)

    saved = {r["id"] for r in _read_scores(settings, "test-run")}
    assert saved == {"q001", "q003", "q004"}  # q002 left for the rerun


def test_one_isolated_parse_failure_does_not_invalidate_a_small_run():
    """At n=8 a single NaN is 12.5%; that is noise, not a broken judge."""
    rows = [_score_row(f"q{i:03d}", f=(math.nan if i == 0 else 0.9)) for i in range(8)]
    summary = build_summary(rows)
    assert summary["overall"]["faithfulness"]["nan"] == 1
    assert summary["overall"]["faithfulness"]["nan_rate"] > NAN_RATE_LIMIT
    assert failed_nan_metrics(summary) == []  # rate exceeded, but only one failure


def test_two_parse_failures_above_the_rate_still_invalidate():
    rows = [_score_row(f"q{i:03d}", f=(math.nan if i < 2 else 0.9)) for i in range(8)]
    assert [k for k, _ in failed_nan_metrics(build_summary(rows))] == ["faithfulness"]


# --- "Request too large": a 429 that retrying can never fix ----------------

GROQ_TOO_LARGE = (
    "Error code: 429 - {'error': {'message': 'Request too large for model "
    "`qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on output "
    "tokens per minute (OTPM): Limit 1000, Requested 1240. The request's expected "
    "output tokens exceed the enforced limit; reduce max_tokens (or the request's "
    "expected output) and try again."
)


def test_request_too_large_is_recognised_and_not_a_rate_limit():
    exc = Exception(GROQ_TOO_LARGE)
    assert is_request_too_large(exc)
    assert is_quota_error(exc)  # it does arrive as a 429
    assert not is_daily_quota_error(exc)  # but it is not a daily cap
    assert not is_request_too_large(Exception(GROQ_OTPM))  # a real rate limit


def test_too_large_row_is_skipped_without_retries(tmp_path, monkeypatch):
    """No backoff loop: one attempt, row not saved, the run carries on."""
    import istqb_rag.eval.step2_judge_scores as step2

    slept = []
    monkeypatch.setattr(step2.time, "sleep", lambda s: slept.append(s))

    settings = make_settings(runs_dir=tmp_path / "runs")
    rows = [_row("q001"), _row("q002")]
    run_generate("test-run", rows, lambda q: _result(row_id=q), settings=settings)

    attempts = []

    def too_large_on_q001(question, response, reference, contexts, metric_keys):
        attempts.append(question)
        if "q001" in question:
            return ScoreOutcome(
                api_error=(
                    "judge request exceeds provider per-request limit — lower JUDGE_MAX_TOKENS"
                )
            )
        return ScoreOutcome(values={k: 0.7 for k in metric_keys})

    stats = run_score("test-run", rows, too_large_on_q001, settings=settings)

    assert slept == []  # never backed off
    assert stats["api_errors"] == 1
    saved = [r["id"] for r in _read_scores(settings, "test-run")]
    assert saved == ["q002"]  # q001 not saved, the run continued past it


def test_retry_loop_does_not_sleep_or_retry_on_a_too_large_request(monkeypatch):
    """The real retry policy short-circuits: one attempt, no backoff."""
    import istqb_rag.eval.step2_judge_scores as step2

    slept, calls = [], []
    monkeypatch.setattr(step2.time, "sleep", lambda s: slept.append(s))

    def run_once():
        calls.append(1)
        raise Exception(GROQ_TOO_LARGE)

    outcome = step2.score_with_retries(run_once, lambda r: {}, step2.JudgeCallCounter())

    assert len(calls) == 1  # exactly one attempt
    assert slept == []
    assert "lower JUDGE_MAX_TOKENS" in outcome.api_error
    assert not outcome.quota_exhausted


def test_retry_loop_does_back_off_on_a_real_rate_limit(monkeypatch):
    """Contrast: a genuine per-minute limit is still retried."""
    import istqb_rag.eval.step2_judge_scores as step2

    slept, calls = [], []
    monkeypatch.setattr(step2.time, "sleep", lambda s: slept.append(s))

    def run_once():
        calls.append(1)
        if len(calls) == 1:
            raise Exception(GROQ_OTPM)
        return "result"

    outcome = step2.score_with_retries(
        run_once, lambda r: {"faithfulness": 1.0}, step2.JudgeCallCounter()
    )
    assert len(calls) == 2 and slept == [0.72]
    assert outcome.values == {"faithfulness": 1.0}


def test_judge_max_tokens_is_passed_to_the_groq_judge():
    """The cap must reach the model, or the provider rejects the request."""
    from istqb_rag.eval.step2_judge_scores import build_judge

    settings = make_settings(judge_model="qwen/qwen3.8-27b", groq_api_key="test-key")
    assert build_judge(settings).max_tokens == settings.judge_max_tokens


# --- Truncated verdicts are not parse failures ----------------------------


def test_truncated_nan_is_counted_apart_from_a_parse_failure():
    rows = [
        _score_row("q001", f=math.nan, truncated=True),  # cap cut it short
        _score_row("q002", f=math.nan, truncated=False),  # unexplained
        _score_row("q003", f=0.9),
    ]
    stats = build_summary(rows)["overall"]["faithfulness"]
    assert stats["nan"] == 2
    assert stats["nan_truncated"] == 1
    assert stats["nan_parse_failure"] == 1
    assert stats["scored"] == 1


def test_truncation_alone_never_invalidates_a_run():
    """Every verdict truncated by our own cap: diagnosed, so the run still stands."""
    rows = [_score_row(f"q{i:03d}", f=math.nan, truncated=True) for i in range(5)]
    rows += [_score_row("q100", f=0.9)]
    summary = build_summary(rows)
    assert summary["overall"]["faithfulness"]["nan_rate"] > NAN_RATE_LIMIT
    assert summary["overall"]["faithfulness"]["parse_failure_rate"] == 0.0
    assert failed_nan_metrics(summary) == []


def test_unexplained_parse_failures_still_invalidate():
    rows = [_score_row(f"q{i:03d}", f=math.nan, truncated=False) for i in range(3)]
    rows += [_score_row(f"q1{i:02d}", f=0.9) for i in range(7)]
    assert [k for k, _ in failed_nan_metrics(build_summary(rows))] == ["faithfulness"]


def test_counter_flags_a_reply_the_cap_cut_short():
    """finish_reason is how truncation is detected, not guesswork."""
    from istqb_rag.eval.step2_judge_scores import JudgeCallCounter

    class _Gen:
        generation_info = {"finish_reason": "length"}
        message = None

    class _Resp:
        llm_output = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 950}}
        generations = [[_Gen()]]

    counter = JudgeCallCounter()
    assert not counter.truncated
    counter.on_llm_end(_Resp())
    assert counter.truncated

"""Eval step 2: have the judge model score the saved answers, and record the scores.

Two stages exist so answers are generated once and can be re-scored later
(Phase 3's judge-variance check depends on it).

Scoring is routed per row (see ``metrics_for``) and each result is appended to
scores.csv immediately, with the same resume behaviour as stage 1. scores.csv
is committed and holds IDs, statuses and scores only — never question,
answer or syllabus text.

Two kinds of NaN are deliberately kept apart:

* an **API error** (the judge never answered) means the row was not measured.
  It is not written, so a rerun retries it.
* a **parse failure** (the judge answered, Ragas could not read it) is a real
  measurement outcome. It is written as an empty cell and counted.
"""

import csv
import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.result_types import RagResult, RetrievedChunk

METRIC_KEYS = ["context_precision", "context_recall", "faithfulness", "response_relevancy"]
RETRIEVAL_METRICS = ["context_precision", "context_recall"]
ANSWER_METRICS = ["faithfulness", "response_relevancy"]

SCORES_COLUMNS = [
    "id",
    "type",
    "chapter",
    "k_level",
    "multi_chunk",
    "status",
    "cited_pages",
    "best_score",
    "context_precision",
    "context_recall",
    "faithfulness",
    "response_relevancy",
    "latency_ms",
    "possible_hallucination",
]

_QUOTA_MARKERS = (
    "resource_exhausted",
    "rate limit",
    "ratelimit",
    "quota",
    "429",
    "too many requests",
)
# A daily cap is worth stopping for; a per-minute bucket just needs a wait.
_DAILY_MARKERS = ("per day", "perday", "requestsperday", "daily limit")
# Anything the provider asks us to wait longer than this is treated as a cap
# we cannot wait out inside one run.
DAILY_WAIT_THRESHOLD_S = 300.0
MAX_ROW_ATTEMPTS = 4
MAX_SLEEP_S = 90.0


class JudgeQuotaExhausted(RuntimeError):
    """The judge refused a call for quota reasons; the score stage must stop."""


def is_quota_error(exc: BaseException) -> bool:
    """True when an exception is the judge refusing on rate-limit grounds."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


def retry_after_seconds(exc: BaseException) -> float | None:
    """How long the provider asked us to wait, in seconds, if it said so.

    Groq phrases it as "try again in 720ms" / "in 1m30s"; Gemini returns a
    retryDelay like "45700s".
    """
    text = str(exc)
    patterns = [
        r"try again in\s+([0-9hms.]+)",
        r"retrydelay['\"]?:\s*['\"]?([0-9hms.]+)",
        r"retry in\s+([0-9hms.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        # Trim the sentence's full stop; "720ms." must not read as 720 minutes.
        raw = match.group(1).strip(".")
        if not raw:
            continue
        ms = re.fullmatch(r"([0-9.]+)ms", raw)
        if ms:
            return float(ms.group(1)) / 1000
        total, found = 0.0, False
        for value, unit in re.findall(r"([0-9.]+)\s*([hms])", raw):
            total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
            found = True
        if found:
            return total
        if raw.replace(".", "").isdigit():
            return float(raw)
    return None


def is_daily_quota_error(exc: BaseException) -> bool:
    """True only for a cap this run cannot wait out — not a per-minute bucket.

    A per-minute token bucket reports a wait of seconds and must be retried;
    treating it as exhaustion would stop the run every few rows and no baseline
    would ever finish.
    """
    if not is_quota_error(exc):
        return False
    text = f"{type(exc).__name__} {exc}".lower()
    if any(marker in text for marker in _DAILY_MARKERS):
        return True
    wait = retry_after_seconds(exc)
    return wait is not None and wait > DAILY_WAIT_THRESHOLD_S


@dataclass
class ScoreOutcome:
    """What one row's scoring produced, and whether it can be trusted."""

    values: dict[str, float] = field(default_factory=dict)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    api_error: str | None = None
    quota_exhausted: bool = False

    @property
    def measured(self) -> bool:
        """Only a row with no API error is a real measurement worth saving."""
        return self.api_error is None and not self.quota_exhausted


# (question, response, reference, contexts, metric_keys) -> ScoreOutcome
ScorerFn = Callable[[str, str, str | None, list[str], list[str]], ScoreOutcome]


class JudgeCallCounter(BaseCallbackHandler):
    """Counts judge calls and tokens, and records the errors Ragas swallows.

    Ragas turns a failed judge call into a NaN score, which is
    indistinguishable from a parse failure. Watching on_llm_error is what lets
    the two be told apart.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.errors: list[BaseException] = []

    def reset(self) -> None:
        self.__init__()

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        self.calls += 1

    def on_chat_model_start(self, serialized: dict, messages: list, **kwargs: Any) -> None:
        self.calls += 1

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
        if not usage:
            for generations in getattr(response, "generations", []) or []:
                for gen in generations:
                    meta = getattr(getattr(gen, "message", None), "response_metadata", {}) or {}
                    usage = meta.get("token_usage") or usage
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.errors.append(error)

    @property
    def quota_exhausted(self) -> bool:
        return any(is_quota_error(e) for e in self.errors)

    @property
    def first_error(self) -> str | None:
        return f"{type(self.errors[0]).__name__}: {self.errors[0]}"[:300] if self.errors else None


def metrics_for(row_type: str, status: str) -> list[str]:
    """Which Ragas metrics a row gets, per the routing table."""
    if row_type != "in_scope" or status == "error":
        return []
    metrics = list(RETRIEVAL_METRICS)
    if status == "answered":
        metrics += ANSWER_METRICS
    return metrics


def scope_verdict(row_type: str, status: str) -> tuple[bool | None, str]:
    """Correctness for scope rows (in_scope rows get (None, "")).

    Returns (correct, flag); a not_in_syllabus row that was answered is a
    possible hallucination.
    """
    if row_type == "out_of_scope":
        return (status in ("refused", "no_context"), "")
    if row_type == "not_in_syllabus":
        if status == "answered":
            return (False, "possible_hallucination")
        return (status in ("refused", "no_context"), "")
    return (None, "")


class _FastEmbedForRagas(Embeddings):
    """FastEmbed embeddings with a string ``.model`` for Ragas.

    Ragas 0.4.3 telemetry builds an EmbeddingUsageEvent with
    ``getattr(embeddings, "model", None)`` and pydantic requires a string.
    FastEmbedEmbeddings.model is a TextEmbedding object, so wrapping it
    directly makes every embedding-backed metric return NaN.
    """

    def __init__(self, model_name: str) -> None:
        from langchain_community.embeddings import FastEmbedEmbeddings

        self.model = model_name
        self._inner = FastEmbedEmbeddings(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(text)


def build_judge(settings: Settings):
    """The judge chat model, chosen by JUDGE_MODEL's provider prefix.

    Gemini names route to Google; everything else is a Groq model id. Either
    way the judge is a different model family from the answer model.
    """
    if settings.judge_model.startswith("gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError("GEMINI_API_KEY is not set — add it to .env (see .env.example).")
        return ChatGoogleGenerativeAI(model=settings.judge_model, temperature=0, api_key=key)

    from langchain_groq import ChatGroq

    key = settings.groq_api_key
    if not key:
        raise ValueError("GROQ_API_KEY is not set — add it to .env (see .env.example).")
    return ChatGroq(model=settings.judge_model, temperature=0, api_key=key)


def make_scorer(settings: Settings | None = None) -> ScorerFn:
    """Build the real Ragas scorer: the configured judge + bge-small embeddings."""
    settings = settings or get_settings()
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.evaluation import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    judge = LangchainLLMWrapper(build_judge(settings))
    embeddings = LangchainEmbeddingsWrapper(_FastEmbedForRagas(settings.embed_model))
    # Ragas' answer_relevancy asks the judge for `strictness` candidates in one
    # call (n=3); the Gemini API rejects n>1 with "Multiple candidates is not
    # enabled for this model". strictness=1 is the supported equivalent.
    answer_relevancy.strictness = 1
    # max_workers=1: one row's ~11k tokens already exceeds the judge's
    # per-minute token bucket, so parallel rows only cause 429s.
    run_config = RunConfig(max_workers=1, max_retries=3, timeout=180)
    ragas_metrics = {
        "context_precision": context_precision,
        "context_recall": context_recall,
        "faithfulness": faithfulness,
        "response_relevancy": answer_relevancy,
    }
    counter = JudgeCallCounter()

    def score_row(
        question: str,
        response: str,
        reference: str | None,
        contexts: list[str],
        metric_keys: list[str],
    ) -> ScoreOutcome:
        sample = SingleTurnSample(
            user_input=question,
            retrieved_contexts=contexts,
            response=response,
            reference=reference,
        )
        dataset = EvaluationDataset(samples=[sample])

        for attempt in range(1, MAX_ROW_ATTEMPTS + 1):
            counter.reset()
            result = None
            error: BaseException | None = None
            try:
                result = evaluate(
                    dataset,
                    metrics=[ragas_metrics[k] for k in metric_keys],
                    llm=judge,
                    embeddings=embeddings,
                    run_config=run_config,
                    callbacks=[counter],
                    show_progress=False,
                )
            except Exception as exc:  # noqa: BLE001 — any judge failure is "not measured"
                error = exc
            else:
                # Ragas swallows per-call failures into NaN; the callback is the
                # only place they are still visible.
                error = counter.errors[0] if counter.errors else None

            if error is None:
                # Ragas names the column after the metric object, which is not
                # always our key: response_relevancy is Ragas' answer_relevancy.
                row_scores = result.to_pandas().iloc[0]
                return ScoreOutcome(
                    values={k: float(row_scores[ragas_metrics[k].name]) for k in metric_keys},
                    calls=counter.calls,
                    prompt_tokens=counter.prompt_tokens,
                    completion_tokens=counter.completion_tokens,
                )

            detail = f"{type(error).__name__}: {error}"[:300]
            if is_daily_quota_error(error):
                return ScoreOutcome(calls=counter.calls, api_error=detail, quota_exhausted=True)

            if is_quota_error(error) and attempt < MAX_ROW_ATTEMPTS:
                wait = min(retry_after_seconds(error) or 5.0 * attempt, MAX_SLEEP_S)
                print(f"  rate limited, waiting {wait:.1f}s (attempt {attempt}/{MAX_ROW_ATTEMPTS})")
                time.sleep(wait)
                continue

            # Out of attempts, or a non-quota failure: not a measurement.
            return ScoreOutcome(calls=counter.calls, api_error=detail)

        return ScoreOutcome(api_error="exhausted judge retries")

    return score_row


def _load_answers(answers_path: Path) -> dict[str, RagResult]:
    answers: dict[str, RagResult] = {}
    if not answers_path.exists():
        return answers
    with answers_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            result = record["result"]
            result["contexts"] = [RetrievedChunk(**c) for c in result["contexts"]]
            answers[record["id"]] = RagResult(**result)
    return answers


def _existing_scores(scores_path: Path) -> set[str]:
    if not scores_path.exists():
        return set()
    with scores_path.open(encoding="utf-8") as f:
        return {row["id"] for row in csv.DictReader(f)}


def run_score(
    run_id: str,
    rows: list[GoldenRow],
    scorer: ScorerFn,
    *,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Score every answered row, appending each scores.csv line immediately.

    Raises JudgeQuotaExhausted as soon as the judge reports a quota failure,
    without writing that row, so rerunning the same command picks up where it
    stopped.
    """
    settings = settings or get_settings()
    run_dir = settings.runs_dir / run_id
    answers = _load_answers(run_dir / "answers.jsonl")
    scores_path = run_dir / "scores.csv"
    done = _existing_scores(scores_path)

    pending = [r for r in rows if r.id not in done and r.id in answers]
    scored = skipped = judged = api_errors = 0
    calls = prompt_tokens = completion_tokens = 0
    quota_error: str | None = None
    write_header = not scores_path.exists()
    with scores_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SCORES_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in rows:
            if row.id in done:
                skipped += 1
                continue
            result = answers.get(row.id)
            if result is None:
                continue
            metrics = metrics_for(row.type, result.status)
            # Out-of-scope and not-in-syllabus rows are judged by the routing
            # table alone, so judge quota cannot affect them. Keep scoring them
            # after a quota stop, or scope accuracy would be unmeasurable on any
            # day the judge budget runs out.
            if quota_error and metrics:
                continue
            outcome = ScoreOutcome()
            if metrics:
                outcome = scorer(
                    row.question,
                    result.answer,
                    row.reference,
                    [c.text for c in result.contexts],
                    metrics,
                )
                calls += outcome.calls
                prompt_tokens += outcome.prompt_tokens
                completion_tokens += outcome.completion_tokens
                if outcome.quota_exhausted:
                    quota_error = outcome.api_error or "judge quota exhausted"
                    continue
                if not outcome.measured:
                    # Not written, so a rerun retries this row.
                    api_errors += 1
                    print(f"[skip] {row.id}: judge API error, not saved ({outcome.api_error})")
                    continue
                judged += 1
            correct, flag = scope_verdict(row.type, result.status)
            record = {
                "id": row.id,
                "type": row.type,
                "chapter": row.chapter if row.chapter is not None else "",
                "k_level": row.k_level or "",
                "multi_chunk": str(row.multi_chunk).lower(),
                "status": result.status,
                "cited_pages": ";".join(str(p) for p in result.cited_pages),
                "best_score": f"{max((c.score for c in result.contexts), default=0.0):.4f}",
                "latency_ms": result.latency_ms["total"],
                "possible_hallucination": flag,
            }
            for key in METRIC_KEYS:
                value = outcome.values.get(key, math.nan)
                record[key] = "" if math.isnan(value) else f"{value:.4f}"
            writer.writerow(record)
            f.flush()
            done.add(row.id)
            scored += 1
            print(f"[{scored}] {row.id}: status={result.status} metrics={len(outcome.values)}")

    if quota_error:
        remaining = len([r for r in pending if r.id not in done])
        print(f"Judge quota exhausted — {remaining} rows left, rerun the same command later.")
        raise JudgeQuotaExhausted(quota_error)

    return {
        "scored": scored,
        "skipped": skipped,
        "judged": judged,
        "api_errors": api_errors,
        "judge_calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }

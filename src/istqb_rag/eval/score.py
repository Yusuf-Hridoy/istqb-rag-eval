"""Stage 2 of the eval: score saved answers with Ragas + the Gemini judge.

Two stages exist so answers are generated once and can be re-scored later
(Phase 3's judge-variance check depends on it).

Scoring is routed per row (see ``metrics_for``) and each result is appended to
scores.csv immediately, with the same resume behaviour as stage 1. scores.csv
is committed and holds IDs, statuses and scores only — never question,
answer or syllabus text.
"""

import csv
import json
import math
import os
from collections.abc import Callable
from pathlib import Path

from langchain_core.embeddings import Embeddings

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.models import RagResult, RetrievedChunk

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

# Scorer signature: (question, response, reference, contexts, metric_keys)
# -> {metric_key: float} where NaN means the judge output could not be parsed.
ScorerFn = Callable[[str, str, str | None, list[str], list[str]], dict[str, float]]


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


def make_scorer(settings: Settings | None = None) -> ScorerFn:
    """Build the real Ragas scorer: Gemini judge + bge-small embeddings."""
    settings = settings or get_settings()
    from langchain_google_genai import ChatGoogleGenerativeAI
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.evaluation import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY is not set — add it to .env (see .env.example).")
    judge = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(model=settings.judge_model, temperature=0, api_key=gemini_key)
    )
    embeddings = LangchainEmbeddingsWrapper(_FastEmbedForRagas(settings.embed_model))
    # Ragas' answer_relevancy asks the judge for `strictness` candidates in one
    # call (n=3); the Gemini API rejects n>1 with "Multiple candidates is not
    # enabled for this model". strictness=1 is the supported equivalent.
    answer_relevancy.strictness = 1
    run_config = RunConfig(max_workers=2, max_retries=3, timeout=180)
    ragas_metrics = {
        "context_precision": context_precision,
        "context_recall": context_recall,
        "faithfulness": faithfulness,
        "response_relevancy": answer_relevancy,
    }

    def score_row(
        question: str,
        response: str,
        reference: str | None,
        contexts: list[str],
        metric_keys: list[str],
    ) -> dict[str, float]:
        sample = SingleTurnSample(
            user_input=question,
            retrieved_contexts=contexts,
            response=response,
            reference=reference,
        )
        dataset = EvaluationDataset(samples=[sample])
        result = evaluate(
            dataset,
            metrics=[ragas_metrics[k] for k in metric_keys],
            llm=judge,
            embeddings=embeddings,
            run_config=run_config,
            show_progress=False,
        )
        row_scores = result.to_pandas().iloc[0]
        # Ragas names the column after the metric object, which is not always our
        # key: response_relevancy is Ragas' answer_relevancy. Look up by .name.
        return {k: float(row_scores[ragas_metrics[k].name]) for k in metric_keys}

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
    """Score every answered row, appending each scores.csv line immediately."""
    settings = settings or get_settings()
    run_dir = settings.runs_dir / run_id
    answers = _load_answers(run_dir / "answers.jsonl")
    scores_path = run_dir / "scores.csv"
    done = _existing_scores(scores_path)

    scored = skipped = judged = 0
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
            values: dict[str, float] = {}
            if metrics:
                values = scorer(
                    row.question,
                    result.answer,
                    row.reference,
                    [c.text for c in result.contexts],
                    metrics,
                )
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
                value = values.get(key, math.nan)
                record[key] = "" if math.isnan(value) else f"{value:.4f}"
            writer.writerow(record)
            f.flush()
            scored += 1
            print(f"[{scored}] {row.id}: status={result.status} metrics={len(values)}")
    return {"scored": scored, "skipped": skipped, "judged": judged}

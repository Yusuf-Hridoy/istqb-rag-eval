"""The RAG pipeline; ``answer(question)`` is the only entry point and never raises."""

import re
import time
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq

from istqb_rag import prompts
from istqb_rag.config import Settings, active_collection, get_settings
from istqb_rag.result_types import RagResult, RetrievedChunk
from istqb_rag.structured_answer import parse_structured_reply

_CITATION_RE = re.compile(r"[\[【]p\.\s*(\d+)[\]】]")
_QUOTES = "\"'“”‘’«»"
_EMPTY_STORE_MESSAGE = "Vector store is empty — run: uv run python -m istqb_rag.ingest"


def parse_cited_pages(text: str) -> list[int]:
    """Extract page numbers from ``[p. N]`` citations, deduplicated, in order."""
    pages: list[int] = []
    for match in _CITATION_RE.finditer(text):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
    return pages


def _normalize(text: str) -> str:
    return text.strip().strip(_QUOTES).rstrip(".!?").strip().casefold()


_REFUSAL_NORMALIZED = _normalize(prompts.REFUSAL_TEXT)
_NOT_FOUND_NORMALIZED = _normalize(prompts.NOT_FOUND_TEXT)


@lru_cache
def _build_llm(settings: Settings) -> BaseChatModel:
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not set — add it to .env (see .env.example).")
    return ChatGroq(
        model=settings.answer_model,
        temperature=0,
        max_retries=3,
        api_key=settings.groq_api_key,
    )


@lru_cache
def _build_store(settings: Settings) -> Chroma:
    return Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=active_collection(settings),
        embedding_function=FastEmbedEmbeddings(model_name=settings.embed_model),
    )


def _error_result(
    question: str,
    model: str,
    total_ms: int,
    exc: Exception,
    retrieve_ms: int = 0,
    generate_ms: int = 0,
    contexts: list[RetrievedChunk] | None = None,
) -> RagResult:
    return RagResult(
        question=question,
        status="error",
        answer="",
        contexts=contexts or [],
        cited_pages=[],
        model=model,
        latency_ms={"retrieve": retrieve_ms, "generate": generate_ms, "total": total_ms},
        error=str(exc),
    )


def answer(
    question: str,
    *,
    llm: BaseChatModel | None = None,
    store: Chroma | None = None,
    settings: Settings | None = None,
) -> RagResult:
    """Answer a single question from the syllabus. Single-turn, no memory."""
    settings = settings or get_settings()
    model = settings.answer_model
    t0 = time.perf_counter()

    if llm is None:
        try:
            llm = _build_llm(settings)
        except Exception as exc:  # missing key, bad config
            return _error_result(question, model, _ms(t0), exc)
    if store is None:
        try:
            store = _build_store(settings)
        except Exception as exc:
            return _error_result(question, model, _ms(t0), exc)

    try:
        if store._collection.count() == 0:
            raise RuntimeError(_EMPTY_STORE_MESSAGE)
    except Exception as exc:
        return _error_result(question, model, _ms(t0), exc)

    t_retrieve = time.perf_counter()
    try:
        scored = store.similarity_search_with_relevance_scores(question, k=settings.top_k)
    except Exception as exc:
        return _error_result(question, model, _ms(t0), exc)
    retrieve_ms = _ms(t_retrieve)

    contexts = [
        RetrievedChunk(
            chunk_id=(doc.metadata.get("chunk_id") or f"p{doc.metadata.get('page', 0)}-?"),
            page=int(doc.metadata.get("page", 0)),
            text=doc.page_content,
            score=round(float(score), 4),
        )
        for doc, score in scored
    ]

    # Below the relevance floor there is no LLM call at all.
    best = max((c.score for c in contexts), default=0.0)
    if best < settings.min_relevance:
        return RagResult(
            question=question,
            status="no_context",
            answer=prompts.NOT_FOUND_TEXT,
            contexts=contexts,
            cited_pages=[],
            model=model,
            latency_ms={"retrieve": retrieve_ms, "generate": 0, "total": _ms(t0)},
        )

    structured = settings.answer_format == "structured"
    if structured:
        system = prompts.STRUCTURED_SYSTEM_PROMPT.format(context=prompts.format_context(contexts))
        generator = llm.bind(response_format={"type": "json_object"})
    else:
        system = prompts.SYSTEM_PROMPT.format(
            refusal=prompts.REFUSAL_TEXT,
            not_found=prompts.NOT_FOUND_TEXT,
            context=prompts.format_context(contexts),
        )
        generator = llm

    t_generate = time.perf_counter()
    try:
        reply = generator.invoke([("system", system), ("human", question)]).content.strip()
    except Exception as exc:
        return _error_result(
            question, model, _ms(t0), exc, retrieve_ms=retrieve_ms, contexts=contexts
        )
    generate_ms = _ms(t_generate)

    # In structured mode the model states its own status; otherwise it is inferred
    # from the two fixed texts, which a differently-worded refusal defeats.
    fallback = False
    if structured:
        parsed = parse_structured_reply(reply)
        if parsed is not None:
            return RagResult(
                question=question,
                status=parsed.status,
                answer=parsed.answer,
                contexts=contexts,
                cited_pages=parsed.cited_pages,
                model=model,
                latency_ms={
                    "retrieve": retrieve_ms,
                    "generate": generate_ms,
                    "total": _ms(t0),
                },
            )
        fallback = True  # unusable JSON: fall through to text matching, and say so

    normalized = _normalize(reply)
    if normalized == _REFUSAL_NORMALIZED:
        status = "refused"
    elif normalized == _NOT_FOUND_NORMALIZED:
        status = "no_context"
    else:
        status = "answered"

    return RagResult(
        question=question,
        status=status,
        answer=reply,
        contexts=contexts,
        cited_pages=parse_cited_pages(reply),
        model=model,
        latency_ms={"retrieve": retrieve_ms, "generate": generate_ms, "total": _ms(t0)},
        format_fallback=fallback,
    )


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)

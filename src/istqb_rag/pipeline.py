"""The RAG pipeline. ``answer(question)`` is the only entry point.

Standard retrieve-then-generate with a single LLM call. Always returns a
RagResult and never raises: errors become status="error" with a message.
"""

import re
import time

from langchain_chroma import Chroma
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq

from istqb_rag import prompts
from istqb_rag.config import Settings, get_settings
from istqb_rag.models import RagResult, RetrievedChunk

_CITATION_RE = re.compile(r"[\[【]p\.\s*(\d+)[\]】]")


def parse_cited_pages(text: str) -> list[int]:
    """Extract page numbers from ``[p. N]`` citations, deduplicated, in order.

    Also accepts the CJK brackets (【p. N】) some models emit."""
    pages: list[int] = []
    for match in _CITATION_RE.finditer(text):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
    return pages


def _build_llm(settings: Settings) -> BaseChatModel:
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not set — add it to .env (see .env.example).")
    return ChatGroq(
        model=settings.answer_model,
        temperature=0,
        max_retries=3,
        api_key=settings.groq_api_key,
    )


def _build_store(settings: Settings) -> Chroma:
    return Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=settings.collection_name,
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

    # 1. Retrieve
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

    # 2. Relevance floor — below it, no LLM call
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

    # 3. Generate
    t_generate = time.perf_counter()
    try:
        reply = llm.invoke(
            [
                (
                    "system",
                    prompts.SYSTEM_PROMPT.format(
                        refusal=prompts.REFUSAL_TEXT,
                        not_found=prompts.NOT_FOUND_TEXT,
                        context=prompts.format_context(contexts),
                    ),
                ),
                ("human", question),
            ]
        ).content.strip()
    except Exception as exc:
        return _error_result(
            question, model, _ms(t0), exc, retrieve_ms=retrieve_ms, contexts=contexts
        )
    generate_ms = _ms(t_generate)

    # 4. Map status from the fixed texts
    if reply == prompts.REFUSAL_TEXT:
        status = "refused"
    elif reply == prompts.NOT_FOUND_TEXT:
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
    )


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)

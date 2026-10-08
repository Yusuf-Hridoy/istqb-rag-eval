"""Offline tests for the answer() pipeline."""

import pytest
from langchain_chroma import Chroma

from istqb_rag import prompts
from istqb_rag.pipeline import answer, parse_cited_pages
from tests.conftest import FakeEmbeddings, SpyLLM, ThrowingLLM, fake_page, make_settings


@pytest.fixture
def store() -> Chroma:
    s = Chroma(embedding_function=FakeEmbeddings(), collection_metadata={"hnsw:space": "cosine"})
    s.add_documents(
        [
            fake_page(42, "Testing shows the presence of defects, not their absence."),
            fake_page(43, "Exhaustive testing is impossible; testing is context dependent."),
        ]
    )
    return s


def test_below_min_relevance_returns_no_context_without_llm_call(store):
    llm = SpyLLM(responses=["should never be used"])
    result = answer("zzz-unrelated topic", llm=llm, store=store, settings=make_settings())

    assert result.status == "no_context"
    assert result.answer == prompts.NOT_FOUND_TEXT
    assert llm.n_calls == 0


def test_status_mapping_refusal(store):
    llm = SpyLLM(responses=[prompts.REFUSAL_TEXT])
    result = answer("any question", llm=llm, store=store, settings=make_settings())
    assert result.status == "refused"


def test_status_mapping_not_found(store):
    llm = SpyLLM(responses=[prompts.NOT_FOUND_TEXT])
    result = answer("any question", llm=llm, store=store, settings=make_settings())
    assert result.status == "no_context"


def test_status_mapping_normalizes_reply(store):
    refusal = prompts.REFUSAL_TEXT
    not_found = prompts.NOT_FOUND_TEXT
    cases = [
        (refusal.rstrip("."), "refused"),  # missing period
        (f'"{refusal}"', "refused"),  # wrapped in quotes
        (refusal.upper(), "refused"),  # different case
        (not_found.rstrip("."), "no_context"),
        (f"'{not_found}'", "no_context"),
        (not_found.lower(), "no_context"),
    ]
    for reply, expected_status in cases:
        llm = SpyLLM(responses=[reply])
        result = answer("any question", llm=llm, store=store, settings=make_settings())
        assert result.status == expected_status, f"reply={reply!r}"


def test_empty_store_returns_error():
    # chromadb's in-memory client is process-wide, so this collection needs a
    # unique name to actually be empty.
    empty = Chroma(
        collection_name="empty-store-test",
        embedding_function=FakeEmbeddings(),
        collection_metadata={"hnsw:space": "cosine"},
    )
    result = answer(
        "any question", llm=SpyLLM(responses=["x"]), store=empty, settings=make_settings()
    )
    assert result.status == "error"
    assert "Vector store is empty" in result.error
    assert "istqb_rag.ingest" in result.error


def test_broken_store_returns_error_not_raise():
    class BrokenCollection:
        def count(self):
            raise RuntimeError("chroma is corrupted")

    class BrokenStore:
        _collection = BrokenCollection()

    result = answer(
        "any question",
        llm=SpyLLM(responses=["x"]),
        store=BrokenStore(),
        settings=make_settings(),
    )
    assert result.status == "error"
    assert "chroma is corrupted" in result.error


def test_status_mapping_answered(store):
    llm = SpyLLM(responses=["Testing shows the presence of defects [p. 42]."])
    result = answer("any question", llm=llm, store=store, settings=make_settings())
    assert result.status == "answered"
    assert result.cited_pages == [42]


def test_llm_exception_becomes_error_result(store):
    llm = ThrowingLLM(responses=[])
    result = answer("any question", llm=llm, store=store, settings=make_settings())

    assert result.status == "error"
    assert "boom" in result.error
    assert result.answer == ""


def test_error_result_when_groq_key_missing(store):
    settings = make_settings(groq_api_key="")
    result = answer("any question", store=store, settings=settings)
    assert result.status == "error"
    assert "GROQ_API_KEY" in result.error


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See [p. 42] for details.", [42]),
        ("First [p. 42] then more [p. 43] and back [p. 42].", [42, 43]),
        ("CJK style 【p. 40】 is parsed too.", [40]),
        ("No citations here.", []),
    ],
)
def test_citation_parsing(text, expected):
    assert parse_cited_pages(text) == expected


def test_result_contract_fields(store):
    llm = SpyLLM(responses=["Answer text [p. 43]."])
    result = answer("q", llm=llm, store=store, settings=make_settings())

    assert result.question == "q"
    assert result.model == "fake-model"
    assert set(result.latency_ms) == {"retrieve", "generate", "total"}
    assert result.error is None
    assert all(c.chunk_id and c.page and c.text and c.score >= 0 for c in result.contexts)

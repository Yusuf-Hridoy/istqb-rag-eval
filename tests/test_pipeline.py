"""Offline tests for the answer() pipeline."""

import pytest
from langchain_chroma import Chroma

from istqb_rag import prompts
from istqb_rag.pipeline import answer, parse_cited_pages
from tests.conftest import FakeEmbeddings, SpyLLM, ThrowingLLM, fake_page, make_settings


@pytest.fixture
def store() -> Chroma:
    s = Chroma(embedding_function=FakeEmbeddings())
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

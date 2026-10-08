"""Shared fakes for offline tests: no API keys, no PDF, no network."""

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from istqb_rag.config import Settings


class FakeEmbeddings(Embeddings):
    """Every document and query matches e1, unless it contains 'zzz-unrelated'."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if "zzz-unrelated" in text:
            return [0.0, 1.0, 0.0]
        return [1.0, 0.0, 0.0]


class SpyLLM(FakeListChatModel):
    n_calls: int = 0

    def _call(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        self.n_calls += 1
        return super()._call(messages, stop=stop, run_manager=run_manager, **kwargs)


class ThrowingLLM(FakeListChatModel):
    def _call(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        raise RuntimeError("boom")


def make_settings(**overrides) -> Settings:
    values = dict(
        groq_api_key="test-key",
        answer_model="fake-model",
        embed_model="fake-embed",
        top_k=2,
        min_relevance=0.3,
        chunk_size=60,
        chunk_overlap=10,
        syllabus_path=Path("data/raw/fake.pdf"),
        first_content_page=None,
        last_content_page=None,
        chroma_dir=Path(".chroma-test"),
        collection_name="test",
        judge_model="fake-judge",
        judge_max_tokens=800,
        chunking="page",
        prompt_version=2,
        score_ok_threshold=0.80,
        answer_format="text",
        golden_path=Path("data/golden_dataset.jsonl"),
        runs_dir=Path("runs-test"),
    )
    values.update(overrides)
    return Settings(**values)


def fake_page(page: int, text: str) -> Document:
    return Document(page_content=text, metadata={"page": page})

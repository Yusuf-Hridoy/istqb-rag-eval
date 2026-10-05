"""Offline tests for syllabus ingestion."""

from langchain_chroma import Chroma

from istqb_rag.ingest import remove_repeated_lines, split_pages
from tests.conftest import fake_page, make_settings


def test_repeated_header_footer_lines_removed_body_kept():
    def make_page(body: str, n: int) -> str:
        return f"ISTQB Certified Tester Foundation Level\nPage {n} of 78\n{body}"

    pages = [
        make_page("1.1 What is testing?\nTesting is important.", 1),
        make_page("1.2 Why is testing?\nBecause defects exist.", 2),
        make_page("1.3 Testing principles\nSeven principles apply.", 3),
    ]
    cleaned = remove_repeated_lines(pages)
    assert all("ISTQB Certified Tester Foundation Level" not in text for text in cleaned)
    assert all("Page " not in text for text in cleaned)
    assert all("1.1 What is testing?" not in text for text in cleaned[1:])
    assert "Because defects exist." in cleaned[1]
    assert "Seven principles apply." in cleaned[2]


def test_unique_lines_kept():
    cleaned = remove_repeated_lines(["unique one", "unique two"])
    assert cleaned == ["unique one", "unique two"]


def test_every_chunk_has_chunk_id_page_and_source():
    settings = make_settings(chunk_size=60, chunk_overlap=10)
    pages = [
        fake_page(41, "word " * 30),  # long enough to split into several chunks
        fake_page(42, "another " * 30),
    ]
    chunks = split_pages(pages, settings)

    assert len(chunks) > 2
    for chunk in chunks:
        assert chunk.metadata["chunk_id"].startswith(f"p{chunk.metadata['page']}-")
        assert chunk.metadata["page"] in (41, 42)
        assert chunk.metadata["source"] == str(settings.syllabus_path)
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_ids must be unique"


def test_chunk_ids_follow_page_sequence():
    settings = make_settings(chunk_size=60, chunk_overlap=10)
    chunks = split_pages([fake_page(42, "alpha " * 40)], settings)
    suffixes = [c.metadata["chunk_id"].removeprefix("p42-") for c in chunks]
    assert suffixes == [str(i + 1) for i in range(len(chunks))]


def test_fake_embeddings_build_in_memory_store():
    from tests.conftest import FakeEmbeddings

    store = Chroma(
        embedding_function=FakeEmbeddings(), collection_metadata={"hnsw:space": "cosine"}
    )
    store.add_documents([fake_page(7, "some content")])
    results = store.similarity_search_with_relevance_scores("some question", k=1)
    assert len(results) == 1
    assert results[0][1] > 0.9

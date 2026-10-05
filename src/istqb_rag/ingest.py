"""Ingest the syllabus PDF into the local Chroma vector store.

Run with: uv run python -m istqb_rag.ingest
Rerunning deletes and rebuilds the collection.
"""

import re
import sys
from collections import Counter

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from istqb_rag.config import Settings, get_settings

DOWNLOAD_URL = "https://www.istqb.org/certifications/certified-tester-foundation-level"

_DIGITS = re.compile(r"\d+")


def _normalized(line: str) -> str:
    """Collapse digit runs so per-page footers like 'Page 12 of 78' count as one line."""
    return _DIGITS.sub("#", line)


def remove_repeated_lines(page_texts: list[str]) -> list[str]:
    """Remove lines that appear on more than half of the pages (headers/footers)."""
    if not page_texts:
        return page_texts
    counts: Counter[str] = Counter()
    for text in page_texts:
        counts.update({_normalized(line.strip()) for line in text.splitlines() if line.strip()})
    boilerplate = {line for line, n in counts.items() if n > len(page_texts) / 2}
    cleaned = []
    for text in page_texts:
        lines = [
            line
            for line in text.splitlines()
            if line.strip() and _normalized(line.strip()) not in boilerplate
        ]
        cleaned.append("\n".join(lines))
    return cleaned


def load_pages(settings: Settings) -> list[Document]:
    """Load the PDF, keep content pages, and strip repeated header/footer lines.

    Returns one document per page with 1-based ``page`` metadata.
    """
    if not settings.syllabus_path.exists():
        sys.exit(
            f"Syllabus PDF not found at {settings.syllabus_path}.\n"
            "Download the ISTQB CTFL v4.0 syllabus from:\n"
            f"  {DOWNLOAD_URL}\n"
            "and save it as:\n"
            f"  {settings.syllabus_path}\n"
            "It is never downloaded automatically."
        )

    docs = PyMuPDFLoader(str(settings.syllabus_path)).load()
    first = settings.first_content_page or 1
    last = settings.last_content_page or max((d.metadata["page"] for d in docs), default=-1) + 1
    content = [d for d in docs if first <= d.metadata["page"] + 1 <= last]
    cleaned = remove_repeated_lines([d.page_content for d in content])
    for doc, text in zip(content, cleaned, strict=True):
        doc.page_content = text
        doc.metadata["page"] = doc.metadata["page"] + 1  # 1-based, as printed in the PDF viewer
    return content


def split_pages(pages: list[Document], settings: Settings) -> list[Document]:
    """Split pages into chunks with chunk_id, page and source metadata."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = []
    for page in pages:
        for i, chunk in enumerate(splitter.split_documents([page]), start=1):
            chunk.metadata["chunk_id"] = f"p{page.metadata['page']}-{i}"
            chunk.metadata["source"] = str(settings.syllabus_path)
            chunks.append(chunk)
    return chunks


def build_store(chunks: list[Document], settings: Settings) -> Chroma:
    """Delete (if present) and rebuild the Chroma collection with cosine distance."""
    store = Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=settings.collection_name,
        embedding_function=FastEmbedEmbeddings(model_name=settings.embed_model),
        collection_metadata={"hnsw:space": "cosine"},
    )
    store.delete_collection()
    store = Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=settings.collection_name,
        embedding_function=FastEmbedEmbeddings(model_name=settings.embed_model),
        collection_metadata={"hnsw:space": "cosine"},
    )
    store.add_documents(chunks)
    return store


def main() -> None:
    settings = get_settings()
    pages = load_pages(settings)
    chunks = split_pages(pages, settings)
    if not chunks:
        sys.exit("No chunks produced — check FIRST_CONTENT_PAGE / LAST_CONTENT_PAGE.")
    build_store(chunks, settings)

    avg_len = sum(len(c.page_content) for c in chunks) / len(chunks)
    print(f"Content pages: {len(pages)}")
    print(f"Chunks: {len(chunks)}")
    print(f"Average chunk length: {avg_len:.0f} characters")
    if len(chunks) < 100:
        print("WARNING: fewer than 100 chunks — check the content page range.")


if __name__ == "__main__":
    main()

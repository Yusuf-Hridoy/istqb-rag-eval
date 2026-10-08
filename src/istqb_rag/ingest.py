"""Ingest the syllabus PDF into the local Chroma vector store."""

import argparse
import dataclasses
import re
import sys
from collections import Counter

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from istqb_rag.config import Settings, active_collection, get_settings

DOWNLOAD_URL = "https://www.istqb.org/certifications/certified-tester-foundation-level"

_DIGITS = re.compile(r"\d+")

# Both heading shapes are matched: the CTFL v4 PDF puts the number and title on
# one line in some places and on separate lines in others.
_HEADING_INLINE = re.compile(r"^(\d+(?:\.\d+){1,2})\.?\s+([A-Z].*)$")
_HEADING_NUMBER_ONLY = re.compile(r"^(\d+(?:\.\d+){1,2})\.?\s*$")
MIN_EXPECTED_SECTIONS = 40


def _normalized(line: str) -> str:
    """Collapse digit runs so per-page footers like 'Page 12 of 78' count as one line."""
    return _DIGITS.sub("#", line)


def is_section_number_line(line: str) -> bool:
    """A bare section number such as "5.1.1." sitting on its own line."""
    return bool(_HEADING_NUMBER_ONLY.match(line.strip()))


def remove_repeated_lines(page_texts: list[str], *, protect=None) -> list[str]:
    """Remove lines that appear on more than half of the pages (headers/footers).

    ``protect`` exempts lines: normalising digit runs collapses every bare
    subsection number to "#.#.#.", which would otherwise be stripped as
    boilerplate and take the section headings with it.
    """
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
            if line.strip()
            and (
                _normalized(line.strip()) not in boilerplate
                or (protect is not None and protect(line))
            )
        ]
        cleaned.append("\n".join(lines))
    return cleaned


def load_pages(settings: Settings) -> list[Document]:
    """Load the PDF, keep content pages, and strip repeated header/footer lines."""
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
    protect = is_section_number_line if settings.chunking == "section" else None
    cleaned = remove_repeated_lines([d.page_content for d in content], protect=protect)
    for doc, text in zip(content, cleaned, strict=True):
        doc.page_content = text
        doc.metadata["page"] = doc.metadata["page"] + 1  # 1-based, as printed in the PDF
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


def find_headings(lines: list[str]) -> list[tuple[int, str, str]]:
    """Locate numbered headings as (line index, section id, heading text)."""
    found = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        inline = _HEADING_INLINE.match(line)
        if inline:
            found.append((i, inline.group(1), f"{inline.group(1)} {inline.group(2)}".strip()))
            continue
        number_only = _HEADING_NUMBER_ONLY.match(line)
        if number_only:
            title = next((x.strip() for x in lines[i + 1 : i + 3] if x.strip()), "")
            if title[:1].isupper():
                found.append((i, number_only.group(1), f"{number_only.group(1)} {title}"))
    return found


def split_sections(pages: list[Document], settings: Settings) -> list[Document]:
    """One chunk per numbered section, with its heading prepended to every piece."""
    lines: list[str] = []
    line_pages: list[int] = []
    for page in pages:
        for line in page.page_content.splitlines():
            lines.append(line)
            line_pages.append(page.metadata["page"])

    headings = find_headings(lines)
    if not headings:
        return []

    # The contents page repeats every section number, so keep the occurrence with
    # the most text under it or a "section" is just a list of titles.
    bounds = {}
    for n, (start, section_id, heading) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        body_len = sum(len(line) for line in lines[start + 1 : end])
        if section_id not in bounds or body_len > bounds[section_id][0]:
            bounds[section_id] = (body_len, start, end, heading)
    headings = sorted(
        ((start, section_id, heading) for section_id, (_, start, _, heading) in bounds.items()),
        key=lambda h: h[0],
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks: list[Document] = []
    for n, (start, section_id, heading) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        body = "\n".join(line for line in lines[start + 1 : end] if line.strip())
        start_page = line_pages[start]
        text = f"{heading}\n{body}".strip()
        meta = {
            "page": start_page,
            "section_id": section_id,
            "source": str(settings.syllabus_path),
        }
        if len(text) <= settings.chunk_size:
            pieces = [text]
        else:
            pieces = [
                f"{heading}\n{piece}" if i else piece
                for i, piece in enumerate(splitter.split_text(text))
            ]
        for i, piece in enumerate(pieces, start=1):
            chunks.append(
                Document(
                    page_content=piece,
                    metadata={**meta, "chunk_id": f"s{section_id}-{i}"},
                )
            )
    return chunks


def build_store(chunks: list[Document], settings: Settings) -> Chroma:
    """Delete (if present) and rebuild the Chroma collection with cosine distance."""
    collection = active_collection(settings)
    store = Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=collection,
        embedding_function=FastEmbedEmbeddings(model_name=settings.embed_model),
        collection_metadata={"hnsw:space": "cosine"},
    )
    store.delete_collection()
    store = Chroma(
        persist_directory=str(settings.chroma_dir),
        collection_name=collection,
        embedding_function=FastEmbedEmbeddings(model_name=settings.embed_model),
        collection_metadata={"hnsw:space": "cosine"},
    )
    store.add_documents(chunks)
    return store


def main() -> None:
    parser = argparse.ArgumentParser(prog="istqb_rag.ingest")
    parser.add_argument(
        "--chunking",
        choices=("page", "section"),
        default=None,
        help="page (default, the Phase 2 baseline) or section",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.chunking:
        settings = dataclasses.replace(settings, chunking=args.chunking)

    pages = load_pages(settings)
    if settings.chunking == "section":
        headings = find_headings(
            [line for page in pages for line in page.page_content.splitlines()]
        )
        chunks = split_sections(pages, settings)
        print(f"Sections detected: {len(headings)}")
        if len(headings) < MIN_EXPECTED_SECTIONS:
            print(
                f"WARNING: only {len(headings)} sections found "
                f"(expected at least {MIN_EXPECTED_SECTIONS}) — heading detection likely failed."
            )
    else:
        chunks = split_pages(pages, settings)

    if not chunks:
        sys.exit("No chunks produced — check FIRST_CONTENT_PAGE / LAST_CONTENT_PAGE.")
    build_store(chunks, settings)

    avg_len = sum(len(c.page_content) for c in chunks) / len(chunks)
    print(f"Chunking mode: {settings.chunking}")
    print(f"Collection: {active_collection(settings)}")
    print(f"Content pages: {len(pages)}")
    print(f"Chunks: {len(chunks)}")
    print(f"Average chunk length: {avg_len:.0f} characters")
    if settings.chunking == "page" and len(chunks) < 100:
        print("WARNING: fewer than 100 chunks — check the content page range.")


if __name__ == "__main__":
    main()

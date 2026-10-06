"""Offline tests for the chat tab's pure helpers.

The Streamlit module is loaded by path because `app/` is not an installed
package. Only the helpers that contain real logic are tested here; the layout
itself was checked by running the app.
"""

import importlib.util
from pathlib import Path

import pytest

from istqb_rag.result_types import RetrievedChunk

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture(scope="module")
def app():
    spec = importlib.util.spec_from_file_location("streamlit_app", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunk(page, score=0.8):
    return RetrievedChunk(chunk_id=f"p{page}-1", page=page, text=f"text {page}", score=score)


# --- Sources split ----------------------------------------------------------


def test_only_cited_chunks_count_as_used(app):
    contexts = [_chunk(15), _chunk(17), _chunk(48)]
    used, other = app.split_sources(contexts, [15, 17])
    assert [c.page for c in used] == [15, 17]
    assert [c.page for c in other] == [48]


def test_nothing_cited_puts_every_chunk_in_other(app):
    contexts = [_chunk(15), _chunk(17)]
    used, other = app.split_sources(contexts, [])
    assert used == []
    assert len(other) == 2


def test_no_chunk_is_dropped_or_duplicated(app):
    """Retrieval is unchanged: the two lists must partition the contexts."""
    contexts = [_chunk(15), _chunk(17), _chunk(48), _chunk(15)]
    used, other = app.split_sources(contexts, [15])
    assert len(used) + len(other) == len(contexts)
    assert [c.page for c in used] == [15, 15]  # duplicate page kept, not merged
    assert [c.page for c in other] == [17, 48]


def test_a_cited_page_that_was_not_retrieved_is_ignored(app):
    used, other = app.split_sources([_chunk(15)], [15, 99])
    assert [c.page for c in used] == [15]
    assert other == []


def test_split_sources_handles_none_cited_pages(app):
    used, other = app.split_sources([_chunk(15)], None)
    assert used == [] and len(other) == 1


# --- Score caption ----------------------------------------------------------


def test_score_caption_is_one_line_with_both_metrics(app):
    caption = app.score_caption({"faithfulness": 0.6666, "response_relevancy": 0.9123})
    assert caption == (
        "Judge: faithfulness 0.67 · relevance 0.91 "
        "(no reference answer, so retrieval metrics aren't scored)"
    )
    assert "\n" not in caption


def test_score_caption_handles_a_missing_metric(app):
    caption = app.score_caption({"faithfulness": 1.0})
    assert "faithfulness 1.00" in caption
    assert "relevance —" in caption

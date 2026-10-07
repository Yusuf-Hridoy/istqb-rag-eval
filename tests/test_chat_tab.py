"""Offline tests for the chat tab's pure helpers.

The Streamlit module is loaded by path because `app/` is not an installed
package. Only the helpers that contain real logic are tested; the layout itself
was checked by reading it.
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


def _chunk(page, text="Some text.", score=0.8):
    return RetrievedChunk(chunk_id=f"p{page}-1", page=page, text=text, score=score)


# --- Only cited chunks are shown --------------------------------------------


def test_only_cited_chunks_are_shown(app):
    contexts = [_chunk(15), _chunk(17), _chunk(48)]
    assert [c.page for c in app.cited_chunks(contexts, [15, 17])] == [15, 17]


def test_nothing_cited_shows_no_source(app):
    assert app.cited_chunks([_chunk(15), _chunk(17)], []) == []
    assert app.cited_chunks([_chunk(15)], None) == []


def test_a_cited_page_that_was_not_retrieved_is_ignored(app):
    assert [c.page for c in app.cited_chunks([_chunk(15)], [15, 99])] == [15]


def test_every_chunk_on_a_cited_page_is_kept(app):
    """Two chunks from the same cited page are both sources, not deduped."""
    contexts = [_chunk(15, "First."), _chunk(15, "Second."), _chunk(48)]
    assert len(app.cited_chunks(contexts, [15])) == 2


# --- Source text is trimmed for display only --------------------------------


def test_leading_partial_sentence_is_dropped(app):
    text = "ing the test object. Testing reduces risk. It is not only execution."
    assert app.trim_to_sentences(text) == "Testing reduces risk. It is not only execution."


def test_trailing_cut_off_sentence_is_dropped(app):
    text = "Testing reduces risk. It is not only execution. Static testing inclu"
    assert app.trim_to_sentences(text) == "Testing reduces risk. It is not only execution."


def test_both_ends_are_trimmed(app):
    text = "ing the object. Testing reduces risk. Static testing inclu"
    assert app.trim_to_sentences(text) == "Testing reduces risk."


def test_a_clean_chunk_is_left_alone(app):
    text = "Testing reduces risk. It is not only execution."
    assert app.trim_to_sentences(text) == text


def test_a_heading_start_is_not_treated_as_a_partial_sentence(app):
    text = "1.3 Testing Principles A number of principles have been suggested."
    assert app.trim_to_sentences(text) == text


def test_whitespace_is_normalised(app):
    assert app.trim_to_sentences("Testing  reduces\n  risk.") == "Testing reduces risk."


def test_text_with_no_sentence_end_is_returned_whole(app):
    """Never show an empty source just because punctuation was missing."""
    assert app.trim_to_sentences("a fragment with no stop") == "a fragment with no stop"


def test_empty_text_is_safe(app):
    assert app.trim_to_sentences("") == ""
    assert app.trim_to_sentences(None) == ""


# --- Score caption ----------------------------------------------------------


def test_score_caption_is_one_line(app):
    caption = app.score_caption({"faithfulness": 0.6666, "response_relevancy": 0.9123})
    assert caption == "Judge: faithfulness 0.67 · relevance 0.91"
    assert "\n" not in caption


def test_score_caption_handles_a_missing_metric(app):
    caption = app.score_caption({"faithfulness": 1.0})
    assert "faithfulness 1.00" in caption and "relevance —" in caption

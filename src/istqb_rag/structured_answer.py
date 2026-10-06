"""Parse the JSON reply the answer model returns in ANSWER_FORMAT=structured mode.

Phase 2 guessed a reply's status by matching it against two fixed sentences, so a
model that refused in its own words was recorded as having answered (q072) and an
answer with no citation went unnoticed (q011). Structured mode asks the model to
say its own status and cite its own pages instead of inferring both from prose.

Parsing is kept here, separate from the pipeline, so it can be tested without a
model and so an unparseable reply has one obvious place to fall back from.
"""

import json
from dataclasses import dataclass, field

# The model's own status vocabulary, mapped to the pipeline's.
_STATUS_MAP = {
    "answered": "answered",
    "refused": "refused",
    "not_found": "no_context",
}


@dataclass
class StructuredReply:
    status: str
    answer: str
    cited_pages: list[int] = field(default_factory=list)
    missing_citation: bool = False


def _as_pages(value: object) -> list[int]:
    """Page numbers from the model, ignoring anything that is not a positive int."""
    if not isinstance(value, list):
        return []
    pages = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            page = item
        elif isinstance(item, str) and item.strip().isdigit():
            page = int(item.strip())
        else:
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def parse_structured_reply(raw: str) -> StructuredReply | None:
    """Turn the model's JSON into a StructuredReply, or None if it is unusable.

    None means the caller should fall back to Phase 2's text matching and set
    ``format_fallback`` on the result. Returning None rather than raising keeps
    a malformed reply from ever failing a run.
    """
    text = (raw or "").strip()
    if text.startswith("```"):  # a fenced block slipped through
        text = text.strip("`")
        text = text.partition("\n")[2] if text[:4].lower().startswith("json") else text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status")
    if not isinstance(status, str) or status.strip().lower() not in _STATUS_MAP:
        return None
    mapped = _STATUS_MAP[status.strip().lower()]

    answer = data.get("answer")
    if not isinstance(answer, str):
        return None

    pages = _as_pages(data.get("cited_pages"))
    return StructuredReply(
        status=mapped,
        answer=answer.strip(),
        cited_pages=pages,
        # The format requires a citation on an answered reply. A missing one is
        # a content problem, not a parse failure, so it is flagged rather than
        # thrown away — the citation-rate metric counts it.
        missing_citation=(mapped == "answered" and not pages),
    )

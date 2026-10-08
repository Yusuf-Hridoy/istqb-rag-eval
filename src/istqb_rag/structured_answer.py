"""Parse the JSON reply the answer model returns in ANSWER_FORMAT=structured mode."""

import json
from dataclasses import dataclass, field

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
    """Turn the model's JSON into a StructuredReply, or None for the caller to fall back."""
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
        # A missing citation is a content problem, not a parse failure: flagged
        # and counted rather than thrown away.
        missing_citation=(mapped == "answered" and not pages),
    )

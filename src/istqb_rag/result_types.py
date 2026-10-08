"""The result objects every part of the app passes around: RetrievedChunk and RagResult.

Ragas reads ``question``, ``answer`` and ``contexts[].text`` by name — do not rename them.
"""

from dataclasses import dataclass
from typing import Literal

Status = Literal["answered", "refused", "no_context", "error"]


@dataclass
class RetrievedChunk:
    chunk_id: str
    page: int
    text: str
    score: float


@dataclass
class RagResult:
    question: str
    status: Status
    answer: str
    contexts: list[RetrievedChunk]
    cited_pages: list[int]  # parsed from [p. N] in the answer
    model: str
    latency_ms: dict[str, int]  # {"retrieve": .., "generate": .., "total": ..}
    error: str | None = None
    # True when structured mode got unparseable JSON and fell back to text matching.
    format_fallback: bool = False

"""Data contracts shared across ingestion, the pipeline, the CLI and the UI.

Phase 2's Ragas runner reads ``question``, ``answer`` and ``contexts[].text``
directly — do not rename those fields.
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

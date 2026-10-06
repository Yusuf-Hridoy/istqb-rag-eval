"""Build the sheet a person reads to label answers, and the empty labels file.

The sheet shows each question, the bot's answer and the exact chunks the bot was
given — and deliberately **not** the judge's scores, so the human label is an
independent opinion rather than a review of the judge's. It contains syllabus
text, so it is gitignored; the labels file it feeds holds only ids and yes/no.
"""

import csv
from pathlib import Path

from istqb_rag.config import Settings, get_settings
from istqb_rag.eval.dataset import GoldenRow
from istqb_rag.eval.step2_judge_scores import _load_answers

LABEL_COLUMNS = ["id", "faithful", "note"]


def labelable_rows(rows: list[GoldenRow], answers: dict) -> list[GoldenRow]:
    """In-scope rows that were actually answered — the only ones faithfulness applies to."""
    return [
        r
        for r in rows
        if r.type == "in_scope" and r.id in answers and answers[r.id].status == "answered"
    ]


def build_sheet(rows: list[GoldenRow], answers: dict, run_id: str) -> str:
    """Markdown: question, answer, and the chunks the answer had to rely on."""
    parts = [
        f"# Human labelling sheet — {run_id}",
        "",
        f"{len(rows)} answered in-scope rows. For each one, decide **only** from the",
        "chunks shown whether every claim in the answer is supported.",
        "",
        "- `faithful = yes` — every claim is supported by the chunks below it.",
        "- `faithful = no` — at least one claim is not. Put the unsupported claim in `note`.",
        "",
        "Judge the answer against the chunks, not against what you know about ISTQB:",
        "a true statement that the chunks do not support is still `no`.",
        "",
        "The judge's scores are deliberately not shown — label first, compare after.",
        "",
        "Record answers in `data/human_labels.csv`.",
        "",
        "---",
        "",
    ]
    for row in rows:
        result = answers[row.id]
        parts += [
            f"## {row.id}",
            "",
            f"**Question:** {row.question}",
            "",
            "**Answer the bot gave:**",
            "",
            "> " + result.answer.replace("\n", "\n> "),
            "",
            f"**Chunks the bot was given** ({len(result.contexts)}):",
            "",
        ]
        for i, chunk in enumerate(result.contexts, start=1):
            parts += [
                f"<details><summary>Chunk {i} — p. {chunk.page}</summary>",
                "",
                "```",
                chunk.text.strip(),
                "```",
                "",
                "</details>",
                "",
            ]
        parts += [f"`{row.id},<yes|no>,<note>`", "", "---", ""]
    return "\n".join(parts)


def write_sheet_and_labels(
    run_id: str,
    rows: list[GoldenRow],
    *,
    settings: Settings | None = None,
) -> tuple[Path, Path, int]:
    """Write the gitignored sheet and the empty, committed labels file."""
    settings = settings or get_settings()
    answers = _load_answers(settings.runs_dir / run_id / "answers.jsonl")
    targets = labelable_rows(rows, answers)

    sheet_path = settings.golden_path.parent / "human_labeling_sheet.md"
    sheet_path.write_text(build_sheet(targets, answers, run_id), encoding="utf-8")

    labels_path = settings.golden_path.parent / "human_labels.csv"
    if labels_path.exists():  # never clobber labels someone has already filled in
        return sheet_path, labels_path, len(targets)
    with labels_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for row in targets:
            writer.writerow({"id": row.id, "faithful": "", "note": ""})
    return sheet_path, labels_path, len(targets)


def load_labels(path: Path) -> dict[str, dict]:
    """{id: {faithful, note}} for rows a human actually filled in."""
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            value = (row.get("faithful") or "").strip().lower()
            if value in ("yes", "no"):
                out[row["id"]] = {"faithful": value, "note": (row.get("note") or "").strip()}
    return out

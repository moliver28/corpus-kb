"""``corpus-kb research ask`` (zero-drift Wave 3B split).

Moved byte-identical from cli.py (the 250-line limit): the grounded
notebook Q&A command, its coroutine, and its human-readable printer.
Registered by cli_research in the original help-output order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID

import typer

from corpus_kb.cli_support import _pid, _run_async, _tenant, wired_pool

if TYPE_CHECKING:
    from corpus_kb.projections.research._embed import ResearchEmbedder

# Module-level singleton (B008): mutable-annotated typer defaults must not
# call typer.Option inline.
_DOC_ID_OPTION = typer.Option(None, "--doc-id", help="Filter: source doc UUID (repeatable)")


def research_ask(
    question: str = typer.Argument(..., help="Notebook question"),
    project_id: str = typer.Option(..., "--project-id", help="Research project UUID"),
    retrieval_only: bool = typer.Option(
        False, "--retrieval-only", help="Return evidence only (zero LLM calls)"
    ),
    k: int = typer.Option(6, "--k", min=1, help="Max evidence exchanges"),
    source_type: str = typer.Option(None, "--source-type", help="Filter: documents.source_type"),
    speaker_role: str = typer.Option(None, "--speaker-role", help="Filter: speaker role"),
    topic_id: int = typer.Option(None, "--topic-id", help="Filter: exchange topic id"),
    doc_id: list[str] = _DOC_ID_OPTION,
    code: str = typer.Option(None, "--code", help="Filter: code name or UUID"),
    min_confidence: str = typer.Option(None, "--min-confidence", help="Filter: high|medium|low"),
    review_status: str = typer.Option(
        None, "--review-status", help="Filter: auto|review|confirmed|overridden"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Grounded notebook Q&A: every sentence carries exchange-level citations."""
    return _run_async(
        _ask_coroutine(
            question,
            project_id,
            retrieval_only,
            k,
            source_type,
            speaker_role,
            topic_id,
            tuple(doc_id or ()),
            code,
            min_confidence,
            review_status,
            json_output,
        )
    )


def _ask_coroutine(
    question: str,
    project_id: str,
    retrieval_only: bool,
    k: int,
    source_type: str | None,
    speaker_role: str | None,
    topic_id: int | None,
    doc_ids: tuple[str, ...],
    code: str | None,
    min_confidence: str | None,
    review_status: str | None,
    json_output: bool,
):
    import json

    async def _run() -> int:
        from corpus_kb.handlers.llm_handler import LlmHandler
        from corpus_kb.research.notebook import NotebookQuery, notebook_ask

        async with wired_pool(embedder=True) as wired:
            llm = None if retrieval_only else LlmHandler(wired.cfg)
            result = await notebook_ask(
                wired.pool,
                cast("ResearchEmbedder", wired.embedder),
                llm,
                NotebookQuery(
                    question=question,
                    tenant_id=_tenant(),
                    project_id=cast(UUID, _pid(project_id)),
                    k=k,
                    retrieval_only=retrieval_only,
                    source_type=source_type,
                    speaker_role=speaker_role,
                    topic_id=topic_id,
                    doc_ids=tuple(cast(UUID, _pid(d)) for d in doc_ids),
                    code=code,
                    min_confidence=min_confidence,
                    review_status=review_status,
                ),
            )
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_ask_result(result)
        if result.get("status") == "error":
            return 2
        return 0

    return _run()


def _print_ask_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    status = str(result.get("status", ""))
    if status == "empty":
        print(guide_copy.NOTEBOOK_EMPTY_HEADER)
        print(str(result.get("message", NO_EVIDENCE_TEXT)))
        return
    evidence = cast("list[dict[str, object]]", result.get("evidence") or [])
    sentences = cast("list[dict[str, object]]", result.get("sentences") or [])
    if sentences:
        for sentence in sentences:
            print(str(sentence.get("text")))
        print()
    if status == "evidence" and result.get("note"):
        print(str(result["note"]))
    print(guide_copy.NOTEBOOK_EVIDENCE_HEADER)
    for item in evidence:
        print(f"[{item.get('n')}] {_ask_locator(item)}")
        answer = str(item.get("highlighted_answer") or item.get("answer") or "")
        print(f"    {answer}")


NO_EVIDENCE_TEXT = "no evidence matches the requested filters"


def _ask_locator(item: dict[str, object]) -> str:
    parts = [str(item.get("title") or item.get("doc_id") or "untitled")]
    if item.get("speaker"):
        parts.append(str(item["speaker"]))
    if item.get("timestamp_s") is not None:
        ts = cast("float", item["timestamp_s"])
        parts.append(f"@ {ts:.0f}s")
    elif item.get("turn_range"):
        lo, hi = cast("list[int]", item["turn_range"])
        parts.append(f"turns {lo}-{hi}")
    return " - ".join(parts)


def register(research_app: typer.Typer) -> None:
    """Attach ``ask`` to the research sub-app (help order: after cycle)."""
    research_app.command("ask")(research_ask)

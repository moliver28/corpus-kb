"""Research-cycle MCP tool (todo 20, v5 §7 postures).

House pattern (tools/research_tools.py): async functions over an asyncpg
pool that MCP wrappers call. ``research_cycle`` is PURE ORCHESTRATION
over the named surfaces; on-mode uses the awaiting_approval receipt as
the MCP approval seam: call again with ``approve=True`` to continue
after each stage boundary (the human confirms in their editor).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

__all__ = ["research_cycle"]

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _status_for(exit_code: int, halted: bool) -> str:
    from corpus_kb.research.cycle_gates import (
        EXIT_APPROVAL_DENIED,
        EXIT_AWAITING_APPROVAL,
        EXIT_UNAVAILABLE,
    )

    if exit_code == 0:
        return "completed"
    if exit_code == EXIT_AWAITING_APPROVAL:
        return "awaiting_approval"
    if exit_code == EXIT_APPROVAL_DENIED:
        return "approval_denied"
    if exit_code == EXIT_UNAVAILABLE:
        return "unavailable"
    return "halted" if halted else "stopped"


async def _approve_all(stage: str) -> bool:
    """MCP on-mode continuation: the caller opted in via approve=True."""
    return True


async def research_cycle(
    pg_pool: asyncpg.Pool,
    mode: str = "out",
    project_id: str | None = None,
    ingest_dir: str | None = None,
    question: str | None = None,
    codebook_version_id: str | None = None,
    guide: bool = False,
    approve: bool = False,
    halt_on: list[str] | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, object]:
    """Run the research cycle; returns a machine-readable receipt.

    mode: "in" (exactly one stage), "on" (one stage per call; approve=True
    carries the cycle through its approval boundaries), or "out"
    (unattended except at halt gates). codebook_promotion is hard-floored:
    the tool never auto-promotes and cannot bypass any governance gate.
    """
    from corpus_kb.config import load_config
    from corpus_kb.research.cycle import run_cycle
    from corpus_kb.research.cycle_state import last_cycle_checkpoint

    exit_code = await run_cycle(
        pg_pool,
        UUID(tenant_id),
        mode=mode,
        project_id=UUID(project_id) if project_id else None,
        ingest_dir=ingest_dir,
        question=question,
        codebook_version_id=UUID(codebook_version_id) if codebook_version_id else None,
        halt_on=halt_on,
        guide=guide,
        json_output=True,
        approve=_approve_all if (mode == "on" and approve) else None,
        cfg=load_config(),
    )
    checkpoint = await last_cycle_checkpoint(pg_pool, UUID(tenant_id))
    halted = str(checkpoint.get("kind")) == "gate_halt"
    gate = checkpoint.get("gate") if halted else None
    return {
        "status": _status_for(exit_code, halted),
        "exit_code": exit_code,
        "gate": gate,
        "checkpoint_kind": str(checkpoint.get("kind", "")),
        "resume_hint": "re-invoke with mode=on&approve=true" if mode == "on" else "",
    }

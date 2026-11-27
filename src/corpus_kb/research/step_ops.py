"""Fail-open op wrappers over the analysis step ledger (TD-13, R11).

Split out of ``step_ledger.py`` so that file stays inside the 250-line
gate. ``run_step`` runs one operation inside a start -> finish/fail
window: the ledger row records the real outcome (``completed`` only after
the op returns, ``failed`` when the op raises), while ledger trouble
never breaks the op — the trace is metadata, never data (the
``OllamaEmbedder`` degrade precedent). The raising lifecycle writers and
the invocation marker (``record_step``) live in
:mod:`corpus_kb.research.step_ledger`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar
from uuid import UUID

from corpus_kb.coding._scope import CodingStore
from corpus_kb.research.step_ledger import fail_step, finish_step, start_step

logger = logging.getLogger(__name__)

__all__ = ["run_step"]

T = TypeVar("T")


async def run_step(
    pool: CodingStore,
    tenant_id: UUID,
    operation: str,
    op: Callable[[], Awaitable[T]],
    *,
    parameters: Mapping[str, Any] | None = None,
    prompt_meta: Mapping[str, Any] | None = None,
) -> T:
    """Run ``op`` inside a start -> finish/fail ledger window; fail-open.

    The row opens ``running`` and completes only after ``op`` returns; a
    raised op records ``failed``, then the error propagates. Ledger
    trouble never breaks the op. ``prompt_meta`` (optional
    ``prompt_id``/``prompt_version``/``prompt_digest``) lands on the
    ledger row's prompt columns (TD-13).
    """
    step_id: UUID | None = None
    meta = dict(prompt_meta or {})
    try:
        step_id = await start_step(
            pool,
            tenant_id,
            operation,
            parameters=parameters,
            prompt_id=meta.get("prompt_id"),
            prompt_version=meta.get("prompt_version"),
            prompt_digest=meta.get("prompt_digest"),
        )
    except Exception:
        logger.warning("Step ledger open degraded for %s", operation, exc_info=True)
    try:
        result = await op()
    except Exception:
        if step_id is not None:
            try:
                await fail_step(pool, tenant_id, step_id)
            except Exception:
                logger.warning("Step ledger fail write degraded for %s", operation, exc_info=True)
        raise
    if step_id is not None:
        try:
            await finish_step(pool, tenant_id, step_id)
        except Exception:
            logger.warning("Step ledger finish degraded for %s", operation, exc_info=True)
    return result

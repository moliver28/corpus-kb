"""Stage 5a: statement atomization (config-gated, additive)."""

from __future__ import annotations

import logging
from typing import Any, cast

from ...statements.atomize import MAX_STATEMENTS_PER_CHUNK
from ...utils.models import Chunk, Entity
from .sinks import IngestSink

logger = logging.getLogger(__name__)


def _stage_max_per_chunk(cfg: dict[str, Any]) -> int:
    """The validated ``statements.max_per_chunk``, or the shipped default.

    The validator (validate_configs) enforces int >= 1 when the key is
    present; a missing or unreadable key falls back to the constant the
    atomizer shipped with, so the cap never silently disappears.
    """
    raw = cfg.get("max_per_chunk")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return raw
    return MAX_STATEMENTS_PER_CHUNK


async def _stage_atomize(
    ingest_sink: IngestSink,
    chunks: list[Chunk],
    entities: list[Entity],
    config: dict[str, Any],
    tenant_id: str,
    errors: list[str],
) -> dict[str, Any]:
    """Stage 5: atomize chunks into classified statement atoms (additive).

    Config-gated (``statements.enabled``) and failure-isolated: any error
    lands in ``errors`` and the summary reports zero stored — the ingest
    result never pretends atoms exist when the stage did not run.
    ``statements.max_per_chunk`` caps the atoms per chunk (default 12).
    """
    cfg = cast(dict[str, Any], config.get("statements") or {})
    if cfg.get("enabled") is not True:
        return {"status": "disabled", "stored": 0}
    try:
        from ...statements import (
            StatementStore,
            atomize_chunk,
            load_templates,
            template_histogram,
        )

        catalogue = load_templates(
            cast("str", cfg.get("templates_dir") or "config/claim_templates")
        )
        max_per_chunk = _stage_max_per_chunk(cfg)
        atoms = [
            atom
            for chunk in chunks
            for atom in atomize_chunk(chunk, entities, catalogue, max_per_chunk=max_per_chunk)
        ]
        store = StatementStore(ingest_sink.gateway, tenant_id)
        stored = await store.store_statements(atoms)
        return {"status": "success", "stored": stored, "templates": template_histogram(atoms)}
    except Exception as exc:
        logger.warning("Statement atomization failed: %s", exc)
        errors.append(f"AtomizeError: {exc}")
        return {"status": "failed", "stored": 0}

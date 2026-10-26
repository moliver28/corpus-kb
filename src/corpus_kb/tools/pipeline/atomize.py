"""Stage 5a: statement atomization (config-gated, additive)."""

from __future__ import annotations

import logging
from typing import Any, cast

from ...utils.models import Chunk, Entity
from .sinks import IngestSink

logger = logging.getLogger(__name__)


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
        atoms = [atom for chunk in chunks for atom in atomize_chunk(chunk, entities, catalogue)]
        store = StatementStore(ingest_sink.gateway, tenant_id)
        stored = await store.store_statements(atoms)
        return {"status": "success", "stored": stored, "templates": template_histogram(atoms)}
    except Exception as exc:
        logger.warning("Statement atomization failed: %s", exc)
        errors.append(f"AtomizeError: {exc}")
        return {"status": "failed", "stored": 0}

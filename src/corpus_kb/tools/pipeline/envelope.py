"""Envelope builders for the pipeline stages (W4-4 split).

The skip envelope, the ledger-stages JSONB, and the run-result envelope
moved verbatim from ``stages.py`` so that file stays under the 250-line
soft limit. ``_claimed_envelope`` is the W4 addition: the dedup skip
branch's twin for a cross-project hash match. ``run_pipeline`` (plan.py)
decorates the returned envelope with strategy provenance keys; these
builders own the pipeline envelope itself.
"""

from __future__ import annotations

from typing import Any

from .document import IngestDocument
from .produce import StageArtifacts
from .result import _stage_result_dict

__all__ = ["_claimed_envelope", "_envelope_from", "_ledger_stages", "_skipped_envelope"]


def _skipped_envelope(doc: IngestDocument, unchanged_doc: str) -> dict[str, Any]:
    """The unchanged-content skip envelope (no ledger row is opened)."""
    return {
        "status": "skipped",
        "document_id": unchanged_doc,
        "path": doc.path,
        "source_type": doc.source_type,
        "reason": "unchanged",
        "errors": [],
    }


def _claimed_envelope(
    doc: IngestDocument, unchanged_doc: str, source_project_id: str | None
) -> dict[str, Any]:
    """The claimed envelope: an existing document joins the calling project.

    The claim branch (W4, F-06) fires on a hash match whose origin differs
    from the requesting project. The origin document stays put; membership
    rides the project_claims row the branch wrote before this envelope.
    """
    return {
        "status": "claimed",
        "document_id": unchanged_doc,
        "path": doc.path,
        "source_type": doc.source_type,
        "reason": "claimed",
        "source_project_id": source_project_id,
        "errors": [],
    }


def _ledger_stages(
    artifacts: StageArtifacts,
    errors: list[str],
    validation_report: dict[str, Any] | None,
    validation_mode: str,
    calibration_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """The ledger stages JSONB: stage counts plus additive payloads.

    The shadow keys appear only when the hierarchy flag is on. Wave 4
    calibrate mode: the validation report rides the ledger stages JSONB
    (the Wave 7 calibration feed) without blocking and without touching
    the envelope. Wave 7: the calibration sweep summary rides it the same
    way; a refused sweep writes nothing (its reason went to the log).
    """
    stages: dict[str, Any] = {
        "chunks": artifacts.pg_chunk_count,
        "vectors": artifacts.pg_vector_count,
        "entities": len(artifacts.entities),
        "relations": len(artifacts.relations),
        "degraded": artifacts.degraded,
        "extractor_id": artifacts.extractor_id,
        "errors": errors,
    }
    if artifacts.hierarchy_settings.enabled:
        stages["shadow_chunk_count"] = artifacts.shadow_chunk_count
        stages["shadow_vector_count"] = artifacts.shadow_vector_count
    if validation_mode == "calibrate" and validation_report is not None:
        stages["validation"] = validation_report
    if calibration_summary:
        stages["calibration"] = calibration_summary
    return stages


def _envelope_from(
    doc: IngestDocument,
    artifacts: StageArtifacts,
    errors: list[str],
    run_id: str,
    title: str | None,
) -> dict[str, Any]:
    """The run result envelope, built from the canonical document."""
    return _stage_result_dict(
        document=doc.document,
        chunks=doc.chunks,
        source_type=doc.source_type,
        path=doc.path,
        degraded=artifacts.degraded,
        embed_note=artifacts.embed_note,
        embed_meta=artifacts.embed_meta,
        empty_reason=doc.empty_reason,
        hierarchy_settings=artifacts.hierarchy_settings,
        shadow_chunk_count=artifacts.shadow_chunk_count,
        shadow_vector_count=artifacts.shadow_vector_count,
        shadow_note=artifacts.shadow_note,
        entities=artifacts.entities,
        relations=artifacts.relations,
        extractor_id=artifacts.extractor_id,
        stats=artifacts.stats,
        pg_chunk_count=artifacts.pg_chunk_count,
        pg_chunk_total=artifacts.pg_chunk_total,
        pg_vector_count=artifacts.pg_vector_count,
        errors=errors,
        run_id=run_id,
        statements=artifacts.statements,
        title=title,
    )

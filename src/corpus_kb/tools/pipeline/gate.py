"""The pre-ledger validation gate: mode resolution, validators, the sweep.

``_validation_gate`` owns everything that runs before the advisory lock:
the validate-mode resolution, the read-only validators, the lock check
for calibrated profiles, and the calibration sweep. ``run_pipeline``
calls this one helper and stores nothing itself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .document import IngestDocument
from .validate import resolve_mode, validate_document

logger = logging.getLogger(__name__)


def _check_profile_lock(config: dict[str, Any], profile: Any) -> None:
    """Enforce aid (Wave 7): fail when a calibrated profile drifted."""
    from corpus_kb.eval.lockfile import check_profile_lock
    from corpus_kb.profiles.loader import profiles_dir as profiles_root

    root = Path(profiles_root(config))
    check_profile_lock(root / f"{profile.name}.yaml", root / f"{profile.name}.lock.json")


async def _validation_gate(
    *,
    document: Any,
    text: str,
    file_hash: str,
    source_type: str,
    path: str,
    is_file: bool,
    chunks: Any,
    empty_reason: str | None,
    resolution: Any,
    config: dict[str, Any],
    ingestion_profile: Any,
    validate_mode: str | None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    """The pre-ledger gate (Waves 4/6/7): validators plus the calibrate sweep.

    Mode precedence: an explicit caller mode wins; otherwise the profile's
    ``validate`` value decides; otherwise off. Per-mode effect: ``off``
    runs nothing and the envelope stays clean; ``advisory`` attaches the
    ``validation`` summary to the envelope; ``enforce`` raises the typed
    ValidationError when a validator fails; ``calibrate`` records the
    summary in the ingest_runs ledger stages without blocking.

    Enforce compares a calibrated profile to its lock and raises on drift,
    so nothing is written. The sweep runs only in calibrate mode with a
    profile that carries a ``calibrate:`` block; its summary rides the
    ledger stages, never the envelope. ``chunks`` is the post-dedup list,
    so the validators see exactly what would be stored.
    """
    if validate_mode is None:
        validate_mode = ingestion_profile.validate_mode if ingestion_profile is not None else "off"
    mode = resolve_mode(validate_mode)
    if mode == "enforce" and ingestion_profile is not None:
        _check_profile_lock(config, ingestion_profile)
    validation_report: dict[str, Any] | None = None
    if mode != "off":
        probe = IngestDocument(
            document=document,
            text=text,
            file_hash=file_hash,
            source_type=source_type,
            path=path,
            is_file=is_file,
            chunks=chunks,
            empty_reason=empty_reason,
            strategy=resolution.strategy.name,
        )
        validation_report = validate_document(probe, mode)
    calibration_summary: dict[str, Any] | None = None
    if (
        mode == "calibrate"
        and ingestion_profile is not None
        and ingestion_profile.calibrate is not None
    ):
        from corpus_kb.tools.pipeline.calibrate_engine import maybe_calibrate

        calibration = await maybe_calibrate(ingestion_profile, config)
        if calibration is not None and calibration.status != "success":
            logger.warning("Calibration sweep refused: %s", calibration.reason)
        if calibration is not None and calibration.summary:
            calibration_summary = dict(calibration.summary)
    return validation_report, mode, calibration_summary

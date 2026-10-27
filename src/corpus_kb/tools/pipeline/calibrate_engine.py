"""The calibration sweep entry point: run it when the profile asks.

``maybe_calibrate`` is the ONE seam the pipeline calls (``validate=calibrate``
mode). It resolves the profile's ``calibrate:`` block, loads the golden
labels and sample files, and drives ``run_calibration`` over a scratch
tenant engine. The engine itself lives in ``calibrate_sweep_engine``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from corpus_kb.errors import ValidationError
from corpus_kb.eval.calibrate import CalibrationResult, run_calibration
from corpus_kb.eval.golden import golden_digest, load_golden
from corpus_kb.eval.lockfile import profile_file_sha256
from corpus_kb.profiles.loader import IngestionProfile, profiles_dir

from .calibrate_sweep_engine import ScratchTenantEngine, tenant_for

__all__ = ["maybe_calibrate"]

logger = logging.getLogger(__name__)


async def maybe_calibrate(
    profile: IngestionProfile, config: dict[str, Any]
) -> CalibrationResult | None:
    """Run the sweep when the profile carries a ``calibrate:`` block.

    Returns None when the profile has no sweep to run. Called from
    ``run_pipeline`` in ``validate=calibrate`` mode, before the advisory
    lock opens: the sweep is slow and writes only under its synthetic
    tenants plus the lock file. Every tenant is cleaned in the finally
    block on success, failure, and cancel alike.
    """
    spec = profile.calibrate
    if spec is None:
        return None
    root = Path(profiles_dir(config))
    profile_path = root / f"{profile.name}.yaml"
    golden_path = root / spec.golden
    sample_paths = _sample_files(root / spec.sample)
    digest = golden_digest(golden_path)
    records = load_golden(golden_path)
    opened: list[ScratchTenantEngine] = []
    try:

        async def factory(candidate: str) -> ScratchTenantEngine:
            engine = ScratchTenantEngine(
                config, tenant_for(profile.name, digest, candidate), min_overlap=spec.min_overlap
            )
            # Track the engine BEFORE open(): a failed open must still get
            # cleanup and close in the finally block below.
            opened.append(engine)
            await engine.open()
            return engine

        return await run_calibration(
            spec,
            golden=records,
            golden_sha256=digest,
            sample_paths=sample_paths,
            engine_factory=factory,
            cfg=config,
            profile_name=profile.name,
            profile_sha256=profile_file_sha256(profile_path),
            lock_path=root / f"{profile.name}.lock.json",
        )
    finally:
        for engine in opened:
            try:
                await engine.cleanup()
            except Exception as exc:  # cleanup must never mask the sweep result
                logger.warning(
                    "Calibration tenant cleanup failed for tenant %s: %s",
                    engine._tenant,
                    exc,
                )
            await engine.close()


def _sample_files(sample: Path) -> list[Path]:
    """The sample files: one file, or every file directly under a directory.

    Dotfiles are skipped: editors and systems drop hidden cruft (``.DS_Store``,
    swap files) into sample folders, and none of it is a transcript.
    """
    if sample.is_file():
        return [sample]
    if sample.is_dir():
        return sorted(
            path for path in sample.iterdir() if path.is_file() and not path.name.startswith(".")
        )
    raise ValidationError(
        f"calibrate.sample path not found: {sample}",
        remediation="Point calibrate.sample at the transcript file or folder to sweep.",
    )

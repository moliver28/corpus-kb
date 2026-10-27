"""The calibration lock file: build, write, and enforce (Wave 7).

``<profile>.lock.json`` records what calibration proved: the profile and
golden hashes, the sample manifest, every candidate's metrics, and the
chosen candidate. The authored YAML is never edited automatically —
``enforce`` mode instead compares the file's current hash against the
lock and fails loudly on drift, so a locked profile cannot change
silently.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_kb.errors import ValidationError
from corpus_kb.eval.labels import overlap_rule
from corpus_kb.ingestion.tokenizer import tokenizer_name

if TYPE_CHECKING:
    from corpus_kb.eval.calibrate import CandidateScore
    from corpus_kb.profiles.loader import CalibrateSpec

__all__ = ["build_lock", "check_profile_lock", "profile_file_sha256"]


def profile_file_sha256(path: Path) -> str:
    """SHA-256 over the raw bytes of one file (manifests and locks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_profile_lock(profile_path: Path, lock_path: Path) -> None:
    """Enforce aid: fail when the authored profile drifted from its lock."""
    if not lock_path.is_file():
        return
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    recorded = lock.get("profile_sha256")
    if not isinstance(recorded, str):
        return
    current = profile_file_sha256(profile_path)
    if current != recorded:
        raise ValidationError(
            f"Profile {profile_path.name} no longer matches its calibration lock.",
            remediation=(
                f"The file hashes to {current[:12]}... but the lock recorded "
                f"{recorded[:12]}.... Re-run calibrate to refresh the lock, or "
                "revert the profile edit."
            ),
        )


def build_lock(
    *,
    spec: CalibrateSpec,
    rows: Sequence[CandidateScore],
    chosen: str,
    cfg: Mapping[str, object],
    profile_name: str,
    profile_sha256: str,
    golden_sha256: str,
    sample_paths: Sequence[Path],
) -> dict[str, object]:
    """The lock payload. Only ``date`` moves between two identical sweeps."""
    raw = cfg.get("embedding")
    embedding: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
    try:
        package_version: str = version("corpus-kb")
    except PackageNotFoundError:  # pragma: no cover - only on a bare checkout
        package_version = "unknown"
    return {
        "profile": profile_name,
        "profile_sha256": profile_sha256,
        "golden_sha256": golden_sha256,
        "sample": [
            {"path": path.name, "sha256": profile_file_sha256(path)} for path in sample_paths
        ],
        "rule": overlap_rule(spec.min_overlap),
        "k": spec.k,
        "min_overlap": spec.min_overlap,
        "candidates": [asdict(row) for row in rows],
        "chosen": chosen,
        "proposed_default_strategy": chosen,
        "embedding": {
            "model": embedding.get("model", "unknown"),
            "dimensions": embedding.get("dimensions", "unknown"),
        },
        "tokenizer": tokenizer_name(dict(cfg)) or "chars/4 heuristic",
        "versions": {"corpus-kb": package_version, "python": sys.version.split()[0]},
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }

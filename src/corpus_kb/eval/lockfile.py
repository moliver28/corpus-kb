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
    from corpus_kb.eval.scoring import CandidateScore
    from corpus_kb.profiles.calibrate_spec import CalibrateSpec

__all__ = ["build_lock", "check_profile_lock", "profile_file_sha256", "write_routing_lock"]


def profile_file_sha256(path: Path) -> str:
    """SHA-256 over the raw bytes of one file (manifests and locks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_profile_lock(profile_path: Path, lock_path: Path) -> None:
    """Enforce aid: fail when the authored profile drifted from its lock.

    A MISSING lock file means no enforcement (nothing was calibrated yet).
    An UNREADABLE lock — broken JSON, a non-object, or a missing
    ``profile_sha256`` string — is a loud ValidationError, never a silent
    pass: enforcement that cannot read its lock must not look like a pass.
    """
    if not lock_path.is_file():
        return
    try:
        lock: object = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(
            f"Calibration lock {lock_path.name} is not readable JSON.",
            remediation="Re-run calibrate to regenerate the lock.",
        ) from exc
    if not isinstance(lock, dict):
        raise ValidationError(
            f"Calibration lock {lock_path.name} is not a JSON object.",
            remediation="Re-run calibrate to regenerate the lock.",
        )
    recorded = lock.get("profile_sha256")
    if not isinstance(recorded, str):
        raise ValidationError(
            f"Calibration lock {lock_path.name} has no profile_sha256 string.",
            remediation="Re-run calibrate to regenerate the lock.",
        )
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
    baseline: str,
    alpha: float,
    min_queries: int,
    outcome: str,
    proposed_overrides: Mapping[str, object],
    reranker_active: bool,
) -> dict[str, object]:
    """The lock payload. Only ``date`` moves between two identical sweeps.

    The recipe keys (``baseline``, ``alpha``, ``min_queries``, ``outcome``,
    ``proposed_overrides``, ``reranker_active``) are additive: enforcement
    reads only ``profile_sha256``, so older locks stay valid.
    """
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
        "baseline": baseline,
        "alpha": alpha,
        "min_queries": min_queries,
        "outcome": outcome,
        "proposed_overrides": dict(proposed_overrides),
        "reranker_active": reranker_active,
        "embedding": {
            "model": embedding.get("model", "unknown"),
            "dimensions": embedding.get("dimensions", "unknown"),
        },
        "tokenizer": tokenizer_name(dict(cfg)) or "chars/4 heuristic",
        "versions": {"corpus-kb": package_version, "python": sys.version.split()[0]},
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def write_routing_lock(
    *,
    scored: Sequence[CandidateScore],
    chosen: str,
    winner_overrides: Mapping[str, object],
    family: Sequence[str],
    recipes: Mapping[str, Mapping[str, object]],
    cfg: Mapping[str, object],
    golden_sha256: str,
    lock_path: Path,
    profile_name: str | None,
    profile_sha256: str | None,
    alpha: float,
    min_queries: int,
) -> dict[str, object]:
    """Build, mark, and write the ROUTING sweep's lock atomically.

    The routing sweep reuses ``build_lock`` with an empty sample (routing
    ingests nothing) and adds two marks on top of the shared shape:
    ``sweep: "routing"`` and ``metric``. Enforcement reads only
    ``profile_sha256``, so the marks never break the apply flow.
    """
    # Lazy import: calibrate reads build_lock from this module lazily too,
    # so neither module loads the other at import time.
    from corpus_kb.eval.calibrate import reranker_active
    from corpus_kb.profiles.calibrate_spec import CalibrateSpec

    if profile_name is None or profile_sha256 is None:
        raise ValidationError(
            "Writing a routing lock needs the profile name and hash.",
            remediation="Pass profile_name and profile_sha256, or drop lock_path.",
        )
    spec = CalibrateSpec(
        candidates=tuple(family),
        recipes={name: dict(recipe) for name, recipe in recipes.items()},
        baseline=family[0],
        sample="(routing sweeps ingest no sample)",
        golden="(the routing golden bench)",
        k=1,
        min_queries=min_queries,
    )
    lock = build_lock(
        spec=spec,
        rows=scored,
        chosen=chosen,
        cfg=cfg,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        golden_sha256=golden_sha256,
        sample_paths=(),
        baseline=family[0],
        alpha=alpha,
        min_queries=min_queries,
        outcome="success",
        proposed_overrides=winner_overrides,
        reranker_active=reranker_active(cfg, winner_overrides),
    )
    lock["sweep"] = "routing"
    lock["metric"] = "primary accuracy"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(lock_path)
    return {key: value for key, value in lock.items() if key != "date"}

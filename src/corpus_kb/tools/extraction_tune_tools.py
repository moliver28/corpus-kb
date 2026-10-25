"""MCP/HTTP/socket/CLI handler for ``extraction_tune`` (Living Corpus W5).

The ONE tune path (zero-drift rule 1): mine confirmed corpus evidence
(human-resolved conflict statements, then high-confidence relations),
then land it as tuning artifacts. ``method=exemplar`` writes profile
seeds and points the profile at them (the paved ``customized``-mode
ladder); ``method=lora`` trains a GLiNER LoRA adapter through
``extraction.lora_tune``. ``dry_run=True`` previews every count and
path with no writes. Lazy imports keep module load light (P0 rule).
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["extraction_tune"]

_TUNE_METHODS = ("exemplar", "lora")
DEFAULT_OUTPUT_ROOT = "data/tuned"

_EXEMPLAR_ENVELOPE = (
    "status",
    "method",
    "profile",
    "dry_run",
    "examples_used",
    "exemplars_written",
    "seeds_path",
    "adapter_path",
    "steps",
    "notes",
)


def _envelope(**values: object) -> dict[str, object]:
    """One envelope shape, every key always present (None when unused)."""
    result: dict[str, object] = {str(key): None for key in _EXEMPLAR_ENVELOPE}
    result.update(values)
    return result


def _validate(method: str, limit: int, min_confidence: float) -> None:
    if method not in _TUNE_METHODS:
        raise ValidationError(
            f"Unknown tune method: {method}.",
            remediation="Use method=exemplar (profile seeds) or method=lora (adapter training).",
        )
    if not isinstance(limit, int) or limit < 1:
        raise ValidationError(
            "limit must be a positive integer.",
            remediation="Pass a whole number of 1 or more, then retry.",
        )
    if not isinstance(min_confidence, (int, float)) or not 0.0 <= float(min_confidence) <= 1.0:
        raise ValidationError(
            "min_confidence must be between 0.0 and 1.0.",
            remediation="Pass a fraction, e.g. 0.8 (the default).",
        )


def _profiles_dir(config: dict[str, object]) -> str:
    from ..profiles import profiles_dir

    return profiles_dir(dict(config))


def _seeds_name(profile: str | None) -> str:
    return f"{profile or 'default'}.seeds.yaml"


async def _exemplar_arm(
    pg_pool: asyncpg.Pool,
    config: dict[str, object],
    tenant_id: str,
    profile: str | None,
    min_confidence: float,
    limit: int,
    dry_run: bool,
) -> dict[str, object]:
    from ..extraction.icl import mine_exemplars
    from ..extraction.tune_data import mine_confirmed_statements, statements_to_exemplars
    from ..profiles.seeds import point_profile_at_seeds, write_seeds

    dir_path = _profiles_dir(config)
    confirmed = await mine_confirmed_statements(
        pg_pool, tenant_id, limit=limit, min_confidence=min_confidence
    )
    mined = list(statements_to_exemplars(confirmed))
    if len(mined) < limit:
        mined.extend(
            await mine_exemplars(
                pg_pool, tenant_id, limit=limit - len(mined), min_confidence=min_confidence
            )
        )
    exemplars = mined[:limit]
    seeds_name = _seeds_name(profile)
    seeds_path = str(Path(dir_path) / seeds_name)
    notes = [f"mined {len(confirmed)} resolved statements"]
    if dry_run:
        return _envelope(
            status="success",
            method="exemplar",
            profile=profile,
            dry_run=True,
            examples_used=len(exemplars),
            exemplars_written=0,
            seeds_path=seeds_path,
            notes=notes,
        )
    if not exemplars:
        # Nothing mined: write nothing. An empty seeds file would orphan
        # any prior seeds and degrade customized mode for no reason.
        notes.append("no confirmed evidence mined; nothing written")
        return _envelope(
            status="empty",
            method="exemplar",
            profile=profile,
            dry_run=False,
            examples_used=0,
            exemplars_written=0,
            seeds_path=seeds_path,
            notes=notes,
        )
    written_path = write_seeds(dir_path, seeds_name, exemplars)
    profile_path = None
    if profile is not None:
        profile_path, created = point_profile_at_seeds(dir_path, profile, seeds_name)
        notes.append(f"profile {profile} {'created' if created else 'updated'} at {profile_path}")
    return _envelope(
        status="success",
        method="exemplar",
        profile=profile,
        dry_run=False,
        examples_used=len(exemplars),
        exemplars_written=len(exemplars),
        seeds_path=written_path,
        notes=notes,
    )


def _gliner_rows(confirmed: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    """Confirmed statements as GLiNER {tokenized_text, ner} rows."""
    from ..extraction.tune_data import to_gliner_example

    rows: list[dict[str, object]] = []
    for item in confirmed:
        text = str(item.get("text") or "")
        pairs: list[tuple[str, str]] = []
        entities = item.get("entities")
        for entity in entities if isinstance(entities, list) else []:
            if isinstance(entity, dict) and entity.get("name") and entity.get("entity_type"):
                pairs.append((str(entity["name"]), str(entity["entity_type"])))
        row = to_gliner_example(text, pairs)
        if row is not None:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def _lora_arm(
    pg_pool: asyncpg.Pool,
    config: dict[str, object],
    tenant_id: str,
    min_confidence: float,
    limit: int,
    dry_run: bool,
) -> dict[str, object]:
    from ..extraction.lora_tune import run_lora_tune
    from ..extraction.tune_data import mine_confirmed_statements

    confirmed = await mine_confirmed_statements(
        pg_pool, tenant_id, limit=max(limit, 24), min_confidence=min_confidence
    )
    rows = _gliner_rows(confirmed, limit)
    if dry_run:
        return _envelope(
            status="success",
            method="lora",
            dry_run=True,
            examples_used=len(rows),
            notes=["dry run: no adapter trained"],
        )
    run_dir = str(Path(DEFAULT_OUTPUT_ROOT) / f"lora-{tenant_id[:8]}")
    manifest = run_lora_tune(rows, dict(config), run_dir)
    return _envelope(
        status="success",
        method="lora",
        dry_run=False,
        examples_used=len(rows),
        adapter_path=run_dir,
        steps=manifest["steps"],
        notes=[
            "adapter candidate saved; the W7 eval gate decides promotion",
            f"base model {manifest['base_model']}, {manifest['adapter_params']} adapter tensors",
        ],
    )


async def extraction_tune(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    method: str = "exemplar",
    profile: str | None = None,
    min_confidence: float = 0.8,
    limit: int = 8,
    dry_run: bool = False,
) -> dict[str, object]:
    """Mine confirmed corpus evidence into profile seeds or an adapter.

    Returns the tune envelope: ``{status, method, profile, dry_run,
    examples_used, exemplars_written, seeds_path, adapter_path, steps,
    notes}``. Deterministic order (statement_id / relation confidence),
    stable for harness snapshots.
    """
    cfg = dict(config) if isinstance(config, dict) else {}
    _validate(method, limit, min_confidence)
    if method == "exemplar":
        return await _exemplar_arm(
            pg_pool, cfg, tenant_id, profile, float(min_confidence), limit, bool(dry_run)
        )
    return await _lora_arm(pg_pool, cfg, tenant_id, float(min_confidence), limit, bool(dry_run))

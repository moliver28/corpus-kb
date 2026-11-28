"""Model tiers (TD-04): the coding.tier selector over models.tiers, legacy overrides honored."""

from __future__ import annotations

from typing import Any

from corpus_kb.errors import ConfigError

__all__ = [
    "CODER_KEY",
    "MODEL_KEY",
    "MODEL_TIERS_DEFAULTS",
    "TIERS_BLOCK_KEY",
    "TIER_KEY",
    "default_coder_models",
    "resolve_coder_models",
    "resolve_tier",
]

TIER_KEY = "coding.tier"
CODER_KEY = "coding.coder"
MODEL_KEY = "coding.model"
TIERS_BLOCK_KEY = "models.tiers"

#: Shipped models.tiers.* snapshot (TD-04 point 3, S6 2026-11-25); digest fills at runtime.
MODEL_TIERS_DEFAULTS: dict[str, dict[str, Any]] = {
    "small": {
        "provider": "ollama",
        "model": "qwen3:4b",
        "digest": "",
        "context_window": 32768,
        "snapshot_date": "2026-11-25",
    },
    "medium": {
        "provider": "ollama",
        "model": "qwen3:8b",
        "digest": "",
        "context_window": 32768,
        "snapshot_date": "2026-11-25",
    },
    "frontier": {
        "provider": "ollama",
        "model": "qwen3:14b",
        "digest": "",
        "context_window": 32768,
        "snapshot_date": "2026-11-25",
    },
}


def resolve_tier(tier: str, config: dict[str, Any]) -> dict[str, Any]:
    """One tier row; an absent models block falls back to shipped defaults.

    A pre-T1 config with no ``models`` block resolves against the shipped
    snapshot. A PRESENT-but-non-mapping ``models.tiers`` block fails loud
    instead of silently looking shipped (momus one-liner, T1 fix round).
    """
    block = config.get("models")
    tiers = block.get("tiers") if isinstance(block, dict) else None
    if tiers is not None and not isinstance(tiers, dict):
        raise ConfigError(f"{TIERS_BLOCK_KEY} must be a mapping of tier rows")
    if not isinstance(tiers, dict) or tier not in tiers:
        if not isinstance(tiers, dict) and tier in MODEL_TIERS_DEFAULTS:
            return dict(MODEL_TIERS_DEFAULTS[tier])
        raise ConfigError(
            f"{TIERS_BLOCK_KEY} has no {tier!r} row. Set {TIER_KEY} to one of "
            f"{sorted(MODEL_TIERS_DEFAULTS)}."
        )
    row = dict(tiers[tier])
    if not isinstance(row.get("model"), str) or not row["model"]:
        raise ConfigError(f"{TIERS_BLOCK_KEY}.{tier}.model must name a model")
    return row


def resolve_coder_models(
    coding_cfg: dict[str, Any], config: dict[str, Any] | None = None
) -> tuple[str, str]:
    """The (coder, model) pair; an explicit non-empty override wins.

    Empty or None ``coding.model``/``coding.coder`` values count as unset
    (AM-10): the shipped defaults are empty so ``coding.tier`` steers out
    of the box. User-set values still take precedence over the tier.
    """
    model = str(coding_cfg.get("model") or "")
    coder = str(coding_cfg.get("coder") or "")
    if model or coder:
        return coder or model, model or coder
    from corpus_kb.config import get_default_config

    cfg = config if config is not None else get_default_config()
    name = str(resolve_tier(str(coding_cfg.get("tier") or "medium"), cfg)["model"])
    return name, name


def default_coder_models() -> tuple[str, str]:
    from corpus_kb.config import load_config

    cfg = load_config()
    coding = cfg.get("coding")
    return resolve_coder_models(coding if isinstance(coding, dict) else {}, cfg)

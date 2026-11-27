"""Model tiers (TD-04, R3): the ``coding.tier`` selector and ``models.tiers``.

A tier leaf reads as ``models.tiers.small.model`` and so on. Local bands:
small at most 4B parameters, medium above 4B below 27B, frontier 27B or
more. The coder reads its rung from ``coding.tier`` here — the one
selector the five former pin sites resolve through (athena R-17). The
legacy ``coding.model``/``coding.coder`` keys stay honored as explicit
overrides; when neither is set the tier's model is the pick.
"""

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

#: The shipped snapshot (TD-04 point 3, S6 2026-11-25). ``digest`` fills
#: when the runtime reports one; it ships empty, never invented.
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
    """One tier's row out of ``models.tiers``; an unknown name fails loud.

    A config tree with NO ``models`` block at all (a pre-T1 user config,
    or a minimal test tree) resolves against the shipped defaults; an
    EXPLICITLY wrong tier name still refuses.
    """
    block = config.get("models")
    tiers = block.get("tiers") if isinstance(block, dict) else None
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
    """The ``(coder, model)`` pair the coding pipeline runs on.

    An explicit ``coding.model``/``coding.coder`` override wins; otherwise
    ``coding.tier`` resolves through ``models.tiers`` and both tags take
    the tier's model.
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
    """The pair from the loaded runtime config (the wiring sites' helper)."""
    from corpus_kb.config import load_config

    cfg = load_config()
    coding = cfg.get("coding")
    return resolve_coder_models(coding if isinstance(coding, dict) else {}, cfg)

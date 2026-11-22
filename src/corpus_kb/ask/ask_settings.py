"""Typed ``ask.*`` settings (issue #540).

Same house pattern as ``rag/rerank_settings.py`` and
``rag/contextual_settings.py``: the module owns the defaults, a frozen
dataclass carries the typed reads, and a loader validates so the YAML
files, ``config.py`` defaults and this module cannot drift.

Keys:

- ``max_sources``: the cap on how many reranked evidence blocks the ask
  prompt (and the returned ``sources`` list) carries. Default 8. The
  reranker still scores its full candidate set; the cap truncates AFTER
  rerank, in rerank order, so the evidence stays stably ordered. The
  reranked set (``reranking.return_top_k``, default 20) stays available
  to callers that need it — the cap only bounds what reaches the answer
  prompt, which is the dominant input-token cost of one ask call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corpus_kb.errors import ValidationError

DEFAULT_ASK_MAX_SOURCES = 8

ASK_CONFIG_DEFAULTS: dict[str, Any] = {
    "max_sources": DEFAULT_ASK_MAX_SOURCES,
}


@dataclass(frozen=True)
class AskSettings:
    """Typed ``ask.*`` reads."""

    max_sources: int = DEFAULT_ASK_MAX_SOURCES


def _ask_block(config: dict[str, Any]) -> dict[str, Any]:
    block = config.get("ask")
    return block if isinstance(block, dict) else {}


def ask_settings_error(config: dict[str, Any]) -> str | None:
    """Remediation text for invalid ``ask.*`` values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so a
    bad cap fails AT LOAD, never mid-ask.
    """
    value = _ask_block(config).get("max_sources", DEFAULT_ASK_MAX_SOURCES)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return (
            "ask.max_sources: must be a positive int (the cap on reranked "
            f"evidence blocks in the answer prompt); got {value!r}"
        )
    return None


def load_ask_settings(config: dict[str, Any]) -> AskSettings:
    """Read ``ask.*`` with defaults; invalid values raise.

    Raises ``ValidationError`` with the same remediation text load_config
    uses, so direct readers (tests, tools) fail as loudly as a config
    load.
    """
    error = ask_settings_error(config)
    if error is not None:
        raise ValidationError(error)
    block = _ask_block(config)
    return AskSettings(max_sources=int(block.get("max_sources", DEFAULT_ASK_MAX_SOURCES)))

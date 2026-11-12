"""Multi-query rewrites: one user query, n diverse search queries (issue #136).

Design locked in the issue: one LLM call rewrites the query along the
DMQR diversity axes — a paraphrase, sub-question splits, keyword variants.
Pure function of (query, model, seed): temperature 0 plus a seed derived
from the inputs, cached by ``SHA256(query + model + variants)`` so a repeat
query replays the same rewrites (zero-drift rule 9 — snapshot-able).

Fail-open contract (the self-query / zero-vector spirit): any LLM,
network, or parse failure returns None. The caller stays on the
single-query path and records the degraded note. Search never fails
because the rewriter is down.

No rewrite text is ever interpolated into SQL: rewrites flow into the same
parameterized arm queries as the original query text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

import httpx
from ollama import Client, ResponseError

logger = logging.getLogger(__name__)

# The bounded rewrite cache: per-process, SHA256-keyed. A hit only saves
# latency; hit or miss returns the same list, so eviction cannot change
# results.
CACHE_MAX_ENTRIES = 512

_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewrites": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rewrites"],
}

_REWRITE_SYSTEM = (
    "Rewrite the user's search query into diverse search queries. "
    "Write one paraphrase, sub-questions that split the ask, and one "
    "keyword variant. Keep the user's language. "
    'Return JSON only: {"rewrites": ["..."]}. Each rewrite is one '
    "short query."
)


def rewrite_cache_key(query: str, model: str, variants: int) -> str:
    """The cache key: SHA256 over the exact rewrite inputs."""
    material = f"{query}\n{model}\n{variants}".encode()
    return hashlib.sha256(material).hexdigest()


def rewrite_seed(cache_key: str) -> int:
    """A stable int seed for the sampled call, derived from the cache key."""
    return int(cache_key[:8], 16)


def clean_rewrites(raw: str, variants: int, original: str) -> tuple[list[str], bool]:
    """Parse and vet the LLM reply. Returns (rewrites, ok).

    Vetting, in one pass and order-stable: strip, drop empties, drop
    anything equal to the original query (case-insensitive — a copy of the
    original would double its fusion weight), then dedupe, then cap to
    ``variants``. Zero usable rewrites is a degradation, not an error.
    """
    try:
        data = json.loads(raw)
        candidates = data.get("rewrites", [])
        if not isinstance(candidates, list):
            return [], False
    except (json.JSONDecodeError, AttributeError):
        return [], False
    seen: set[str] = set()
    original_key = original.strip().casefold()
    rewrites: list[str] = []
    for candidate in candidates[: variants * 4]:
        text = str(candidate).strip()
        key = text.casefold()
        if not text or key == original_key or key in seen:
            continue
        seen.add(key)
        rewrites.append(text)
        if len(rewrites) == variants:
            break
    return rewrites, bool(rewrites)


class QueryRewriter:
    """One Ollama chat call per query, cached, fail-open to None."""

    def __init__(self, model: str, base_url: str, timeout_seconds: float, variants: int) -> None:
        self.model = model
        self.variants = variants
        self._client = Client(host=base_url, timeout=timeout_seconds)
        self._cache: dict[str, list[str]] = {}

    def rewrites(self, query: str) -> list[str] | None:
        """The rewrites for one query, or None when the rewriter is down.

        Cached by SHA256(query + model + variants): the same query twice
        returns the same list without a second LLM call.
        """
        cache_key = rewrite_cache_key(query, self.model, self.variants)
        hit = self._cache.get(cache_key)
        if hit is not None:
            return list(hit)
        try:
            raw = self._client_chat(query, cache_key)
            rewrites, ok = clean_rewrites(raw, self.variants, query)
        except (
            ConnectionError,
            OSError,
            httpx.NetworkError,
            ResponseError,
            json.JSONDecodeError,
            KeyError,
        ) as exc:
            # ResponseError covers "model not found" — the same
            # fresh-install failure mode the self-query parser degrades on.
            logger.warning("Multi-query rewriter unavailable: %s; single-query path.", exc)
            return None
        if not ok:
            logger.warning("Multi-query rewriter gave no usable rewrites; single-query path.")
            return None
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[cache_key] = rewrites
        return list(rewrites)

    def _client_chat(self, query: str, cache_key: str) -> str:
        response = self._client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": query},
            ],
            format=_REWRITE_SCHEMA,
            options={"temperature": 0, "seed": rewrite_seed(cache_key)},
        )
        message = response.get("message", {}) if isinstance(response, dict) else {}
        return str(cast(dict[str, Any], message).get("content", "{}"))

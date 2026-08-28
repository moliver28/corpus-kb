"""Self-query structured-filter extraction.

Parses a natural-language query into a clean semantic string plus a set of
structured predicates over a fixed whitelist of relational columns, using
Ollama's JSON-schema-constrained chat output (format=<schema>, confirmed
supported by both /api/generate and /api/chat). On any parse/validation/
LLM failure, degrades to the original query with zero predicates -- the
same fail-open contract as OllamaEmbedder's zero-vector degraded mode.

No value from the LLM is ever interpolated into SQL text: only whitelisted
identifiers and a fixed operator set are, and every value is bound as a
parameter by build_filter_sql().
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, cast

import httpx
from ollama import Client, ResponseError

logger = logging.getLogger(__name__)

ALLOWED_COLUMNS: dict[str, dict[str, object]] = {
    "documents.source_type": {"kind": "text", "ops": {"="}},
    "documents.language": {"kind": "text", "ops": {"="}},
    "documents.created_at": {"kind": "date", "ops": {"<", ">", "<=", ">="}},
    "chunks.chunk_type": {"kind": "text", "ops": {"="}},
    "chunks.source_type": {"kind": "text", "ops": {"="}},
}
SPECIAL_TARGETS = {"tag", "metadata"}

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "semantic_query": {"type": "string"},
        "predicates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "op": {"type": "string"},
                    "value": {"type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["target", "op", "value"],
            },
        },
    },
    "required": ["semantic_query", "predicates"],
}


@dataclass(frozen=True)
class Predicate:
    target: str
    op: str
    value: str
    key: Optional[str] = None  # only used for the "metadata" target


@dataclass(frozen=True)
class ParsedQuery:
    semantic_query: str
    predicates: list[Predicate] = field(default_factory=list)


class SelfQueryParser:
    def __init__(self, config: Optional[dict[str, object]] = None) -> None:
        cfg = cast(dict[str, object], ((config or {}).get("search", {}) or {}).get("self_query", {}) or {})
        self.model = str(cfg.get("model", "qwen3:4b"))
        self.base_url = str(cfg.get("base_url", "http://localhost:11434"))
        self.timeout_seconds = float(cfg.get("timeout_seconds", 8))
        self._client = Client(host=self.base_url, timeout=self.timeout_seconds)

    def parse(self, query: str) -> ParsedQuery:
        try:
            raw = self._client_chat(query)
            data = json.loads(raw)
            semantic_query = str(data.get("semantic_query", query))
            predicates = []
            for p in data.get("predicates", []):
                target = str(p.get("target", ""))
                op = str(p.get("op", ""))
                value = str(p.get("value", ""))
                key = p.get("key")
                if target in SPECIAL_TARGETS or (
                    target in ALLOWED_COLUMNS and op in cast(set, ALLOWED_COLUMNS[target]["ops"])
                ):
                    predicates.append(Predicate(target=target, op=op, value=value, key=key))
            return ParsedQuery(semantic_query=semantic_query, predicates=predicates)
        except (ConnectionError, OSError, httpx.NetworkError, ResponseError, json.JSONDecodeError, KeyError) as exc:
            # ResponseError covers "model not found" -- this is the failure
            # mode a fresh install with self_query.enabled=true (the approved
            # default) hits immediately if its configured chat model was
            # never pulled. Must degrade to unfiltered search, not 400 it.
            logger.warning("Self-query parser unavailable/invalid: %s; using unfiltered query.", exc)
            return ParsedQuery(semantic_query=query, predicates=[])

    def _client_chat(self, query: str) -> str:
        system = (
            "Extract a clean semantic search query plus structured filters. "
            f"Allowed filter targets: {list(ALLOWED_COLUMNS.keys())} and 'tag'/'metadata'. "
            "Only emit filters the user's text actually implies. If none, return an empty predicates list."
        )
        response = self._client.chat(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": query}],
            format=_JSON_SCHEMA,
            options={"temperature": 0},
        )
        message = response.get("message", {}) if isinstance(response, dict) else {}
        return str(message.get("content", "{}"))


def build_filter_sql(predicates: list[Predicate], start_index: int) -> tuple[str, list[object]]:
    """Build a parameterized ' AND ...' fragment. Never interpolates raw values."""
    if not predicates:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    idx = start_index
    for pred in predicates:
        if pred.target == "tag":
            clauses.append(
                f"EXISTS (SELECT 1 FROM document_tags dt JOIN tags t ON dt.tag_id = t.tag_id "
                f"WHERE dt.doc_id = c.doc_id AND t.name = ${idx})"
            )
            params.append(pred.value)
            idx += 1
        elif pred.target == "metadata":
            clauses.append(
                f"EXISTS (SELECT 1 FROM metadata m WHERE m.doc_id = c.doc_id "
                f"AND m.key = ${idx} AND m.value = ${idx + 1})"
            )
            params.append(pred.key or "")
            params.append(pred.value)
            idx += 2
        elif pred.target in ALLOWED_COLUMNS and pred.op in cast(set, ALLOWED_COLUMNS[pred.target]["ops"]):
            clauses.append(f"{pred.target} {pred.op} ${idx}")
            params.append(pred.value)
            idx += 1
        # silently drop anything else -- already filtered in parse(), this is defense in depth
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params

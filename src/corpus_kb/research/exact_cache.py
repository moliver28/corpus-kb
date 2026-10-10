"""U42: provenance-keyed exact result cache for LLM coding/judgment calls.

Cache rows are keyed by the SHA-256 of the FULL provenance tuple — unit text
hash, codebook release hash, prompt-template hash, model digest, decoding
params, schema hash — and matched EXACTLY. There is deliberately NO semantic
caching: a near-hit on a coding decision is a false hit. Any component
change derives a new key, so entries are invalidated naturally and never
mutated (migration 020 makes UPDATE/DELETE raise).

Tenant scoping is enforced twice: the ``tenant_id`` predicate in every
statement's WHERE clause here, and RLS (migration 020) under the tenant GUC.

Prompts are normalized STATIC-PREFIX-FIRST (instructions/codebook first,
variable unit last) via :func:`normalize_prompt_static_first` so consecutive
calls share the longest possible literal prompt prefix — the KV-cache reuse
Ollama/llama.cpp get for free on repeated prefixes.

Config (``cache.*``, orchestrator wires YAML at the checkpoint):
``enabled`` master switch — TRUE by default for deductive runs; inductive
discovery runs keep their own FALSE default unless deliberately seeded
(``inductive_enabled: true``), per spec v8 U42.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

# ---------------------------------------------------------------------------
# Config (cache.*)
# ---------------------------------------------------------------------------

DEFAULT_CACHE_ENABLED = True
DEFAULT_DEDUCTIVE_ENABLED = True
DEFAULT_INDUCTIVE_ENABLED = False

CACHE_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_CACHE_ENABLED,
    "deductive_enabled": DEFAULT_DEDUCTIVE_ENABLED,
    "inductive_enabled": DEFAULT_INDUCTIVE_ENABLED,
}


@dataclass(frozen=True)
class CacheSettings:
    """Typed ``cache.*`` reads.

    ``enabled`` gates every cache access (the ``--no-cache`` flag forces
    this False at the CLI layer by dropping the key from the config dict).
    ``deductive_enabled``/``inductive_enabled`` select the per-mode posture:
    deductive coding defaults to caching ON; inductive discovery defaults
    OFF unless seeded.
    """

    enabled: bool = DEFAULT_CACHE_ENABLED
    deductive_enabled: bool = DEFAULT_DEDUCTIVE_ENABLED
    inductive_enabled: bool = DEFAULT_INDUCTIVE_ENABLED

    def allows(self, mode: str) -> bool:
        """True when ``mode`` ('deductive'|'inductive') may read/write cache."""
        if not self.enabled:
            return False
        if mode == "deductive":
            return self.deductive_enabled
        if mode == "inductive":
            return self.inductive_enabled
        raise ValueError(f"unknown cache mode {mode!r}: expected 'deductive' or 'inductive'")


def load_cache_settings(config: dict[str, object]) -> CacheSettings:
    """Read the ``cache.*`` block, defaulting to the module constants."""
    block = config.get("cache", {})
    if not isinstance(block, dict):
        return CacheSettings()
    cfg = cast(dict[str, object], block)
    flags = []
    for name, default in (
        ("enabled", DEFAULT_CACHE_ENABLED),
        ("deductive_enabled", DEFAULT_DEDUCTIVE_ENABLED),
        ("inductive_enabled", DEFAULT_INDUCTIVE_ENABLED),
    ):
        value = cfg.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"cache.{name} must be a bool; got {value!r}")
        flags.append(value)
    return CacheSettings(enabled=flags[0], deductive_enabled=flags[1], inductive_enabled=flags[2])


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheKeyComponents:
    """The six provenance components (v8 U42). Every field feeds the key."""

    unit_text_sha256: str
    codebook_release_sha256: str
    prompt_template_sha256: str
    model_digest: str
    decoding_params_json: str  # canonical JSON (sorted keys) of the decoding params
    schema_sha256: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_decoding_params(params: dict[str, object]) -> str:
    """Canonical JSON for the decoding params (sorted keys, fixed separators)."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def compute_cache_key(components: CacheKeyComponents) -> str:
    """SHA-256 over the canonical JSON of the full provenance tuple."""
    payload = canonical_decoding_params(
        {
            "unit_text_sha256": components.unit_text_sha256,
            "codebook_release_sha256": components.codebook_release_sha256,
            "prompt_template_sha256": components.prompt_template_sha256,
            "model_digest": components.model_digest,
            "decoding_params": components.decoding_params_json,
            "schema_sha256": components.schema_sha256,
        }
    )
    return sha256_text(payload)


def normalize_prompt_static_first(static_prefix: str, variable_unit: str) -> str:
    """Render the prompt with the STATIC prefix first, variable unit LAST.

    Instructions and codebook text go up front and the unit text joins at
    the end, so consecutive calls for different units share one literal
    prefix — maximizing the prompt-prefix (KV-cache) reuse Ollama gets for
    free. Callers must therefore build templates with the unit slot LAST;
    this function is the single choke point that guarantees the order.
    """
    return f"{static_prefix.rstrip()}\n\n{variable_unit}"


# ---------------------------------------------------------------------------
# Storage (asyncpg, tenant-scoped; migration 020 owns the table)
# ---------------------------------------------------------------------------

GET_SQL = (
    "SELECT response, model_name, created_at FROM research_exact_cache "
    "WHERE cache_key = $1 AND tenant_id = $2"
)

PUT_SQL = """
INSERT INTO research_exact_cache
    (cache_key, tenant_id, component_hashes, response, model_name, model_digest)
VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
ON CONFLICT (cache_key) DO NOTHING
"""


class CacheConn(Protocol):
    """Minimal asyncpg.Connection surface the cache needs (test-fake friendly)."""

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        """Run one statement, return the first row or None."""
        ...

    async def execute(self, sql: str, *args: object) -> object:
        """Run one statement."""
        ...


async def get(conn: CacheConn, tenant_id: str, cache_key: str) -> dict[str, object] | None:
    """Return the cached response payload, or None on a miss.

    The tenant predicate rides in the WHERE clause (belt) — RLS (migration
    020) is the braces. A row written by another tenant is unreachable even
    in the astronomically unlikely event of a key collision.
    """
    row = await conn.fetchrow(GET_SQL, cache_key, tenant_id)
    if row is None:
        return None
    # asyncpg.Record satisfies the Mapping protocol; cast (not isinstance)
    # so dict and Record both flow through the same read.
    record = cast("Mapping[str, object]", row)
    response = record.get("response")
    return dict(response) if isinstance(response, dict) else {"response": response}


async def put(
    conn: CacheConn,
    tenant_id: str,
    cache_key: str,
    components: CacheKeyComponents,
    response: dict[str, object],
    model_name: str,
    model_digest: str,
) -> None:
    """Write one immutable cache row (conflict = keep the original)."""
    await conn.execute(
        PUT_SQL,
        cache_key,
        tenant_id,
        json.dumps(
            {
                "unit_text_sha256": components.unit_text_sha256,
                "codebook_release_sha256": components.codebook_release_sha256,
                "prompt_template_sha256": components.prompt_template_sha256,
                "model_digest": components.model_digest,
                "decoding_params": components.decoding_params_json,
                "schema_sha256": components.schema_sha256,
            }
        ),
        json.dumps(response),
        model_name,
        model_digest,
    )

"""U20: transaction-local HNSW settings for pgvector iterative scans.

pgvector >= 0.8 supports ``hnsw.iterative_scan`` (off | relaxed_order |
strict_order). Iterative scans change how filtered ANN queries retrieve
candidates, so the setting MUST be applied per-transaction (``SET LOCAL``)
inside the same transaction that runs the query — never session-wide (a
pooled connection would leak the setting across tenants/requests).

This module owns the whole contract:

* :class:`HnswSettings` — the frozen settings model (defaults follow the
  pgvector-recommended safe posture: ``strict_order`` guarantees strictly
  distance-ordered results over the retrieved candidate set).
* :func:`load_hnsw_settings` — reads ``search.hnsw.*`` from the loaded
  config with these same defaults (config.py ships the identical block so
  the YAML files, the in-code defaults and this module never drift).
* :func:`set_local_statements` — pure SQL emission (empty for ``off``).
* :func:`apply_hnsw_settings` — executes the statements on an OPEN
  transaction-scoped asyncpg connection (the caller owns the transaction,
  mirroring ``storage.tenant_conn.tenant_connection``).
* :func:`supports_iterative_scan` — version gate: the SET LOCAL statements
  error on pgvector < 0.8, so callers MUST gate emission on the installed
  extension version (unknown/unreadable versions disable, never enable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

ITERATIVE_SCAN_MODES = frozenset({"off", "relaxed_order", "strict_order"})

DEFAULT_ITERATIVE_SCAN = "strict_order"
DEFAULT_EF_SEARCH = 200
DEFAULT_MAX_SCAN_TUPLES = 20000

# pgvector 0.8.0 introduced hnsw.iterative_scan (release notes: 2024-04-30).
MIN_ITERATIVE_SCAN_VERSION = (0, 8, 0)


class SqlExecutor(Protocol):
    """Minimal asyncpg.Connection surface the settings emitter needs."""

    async def execute(self, sql: str) -> object:
        """Execute one SQL statement."""
        ...


@dataclass(frozen=True)
class HnswSettings:
    """Transaction-local HNSW scan settings (U20).

    Defaults mirror the pgvector docs' safe posture: ``strict_order``
    returns strictly distance-ordered results at the cost of extra page
    reads; ``ef_search``/``max_scan_tuples`` bound the per-query scan
    effort. ``iterative_scan="off"`` reproduces pre-0.8 behavior exactly.
    """

    iterative_scan: str = DEFAULT_ITERATIVE_SCAN
    ef_search: int = DEFAULT_EF_SEARCH
    max_scan_tuples: int = DEFAULT_MAX_SCAN_TUPLES

    def __post_init__(self) -> None:
        _validate(self.iterative_scan, self.ef_search, self.max_scan_tuples)


def _validate(iterative_scan: str, ef_search: int, max_scan_tuples: int) -> None:
    if iterative_scan not in ITERATIVE_SCAN_MODES:
        modes = ", ".join(sorted(ITERATIVE_SCAN_MODES))
        raise ValueError(
            f"search.hnsw.iterative_scan must be one of ({modes}); got {iterative_scan!r}"
        )
    if ef_search <= 0:
        raise ValueError(f"search.hnsw.ef_search must be a positive int; got {ef_search}")
    if max_scan_tuples <= 0:
        raise ValueError(
            f"search.hnsw.max_scan_tuples must be a positive int; got {max_scan_tuples}"
        )


def load_hnsw_settings(config: dict[str, object]) -> HnswSettings:
    """Read ``search.hnsw.*`` from a loaded config, defaulting safely.

    Missing keys fall back to :class:`HnswSettings` defaults; invalid values
    raise ``ValueError`` (a misconfigured scan mode must fail loudly, not
    silently fall back to a different retrieval posture).
    """
    search = config.get("search", {})
    hnsw: dict[str, object] = {}
    if isinstance(search, dict):
        block = cast(dict[str, object], search).get("hnsw", {})
        if isinstance(block, dict):
            hnsw = cast(dict[str, object], block)

    iterative_scan = str(hnsw.get("iterative_scan", DEFAULT_ITERATIVE_SCAN))
    ef_search = _coerce_positive_int(hnsw.get("ef_search"), DEFAULT_EF_SEARCH, "ef_search")
    max_scan_tuples = _coerce_positive_int(
        hnsw.get("max_scan_tuples"), DEFAULT_MAX_SCAN_TUPLES, "max_scan_tuples"
    )
    _validate(iterative_scan, ef_search, max_scan_tuples)
    return HnswSettings(
        iterative_scan=iterative_scan,
        ef_search=ef_search,
        max_scan_tuples=max_scan_tuples,
    )


def _coerce_positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"search.hnsw.{name} must be an int; got {value!r}")
    if isinstance(value, int):
        coerced = value
    elif isinstance(value, str):
        try:
            coerced = int(value)
        except ValueError as exc:
            raise ValueError(f"search.hnsw.{name} must be an int; got {value!r}") from exc
    else:
        raise ValueError(f"search.hnsw.{name} must be an int; got {value!r}")
    if coerced <= 0:
        raise ValueError(f"search.hnsw.{name} must be a positive int; got {coerced}")
    return coerced


def set_local_statements(settings: HnswSettings) -> tuple[str, ...]:
    """The SET LOCAL statements for ``settings`` (empty tuple for ``off``).

    Literal SQL (no parameters): asyncpg executes these with ``conn.execute``
    inside the caller's transaction. ``off`` emits NOTHING so pre-iterative
    pgvector deployments run byte-identical queries.
    """
    if settings.iterative_scan == "off":
        return ()
    return (
        f"SET LOCAL hnsw.iterative_scan = '{settings.iterative_scan}'",
        f"SET LOCAL hnsw.ef_search = {settings.ef_search}",
        f"SET LOCAL hnsw.max_scan_tuples = {settings.max_scan_tuples}",
    )


async def apply_hnsw_settings(conn: SqlExecutor, settings: HnswSettings) -> bool:
    """Execute the SET LOCAL statements on ``conn`` inside the current transaction.

    The caller MUST have an open transaction (SET LOCAL is a no-op outside
    one — see ``storage.tenant_conn`` for the house transaction pattern).
    Returns True when statements were emitted, False for the ``off`` no-op.
    """
    statements = set_local_statements(settings)
    for statement in statements:
        await conn.execute(statement)
    return bool(statements)


def parse_pgvector_version(extversion: str | None) -> tuple[int, int, int] | None:
    """Parse ``pg_extension.extversion`` (e.g. '0.8.2') or None if unreadable."""
    if not extversion:
        return None
    parts = extversion.strip().split(".")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return None


def supports_iterative_scan(extversion: str | None) -> bool:
    """True only when the installed pgvector provably supports iterative scans.

    Unknown/unreadable versions return False: the feature gate fails CLOSED
    (emitting ``SET LOCAL hnsw.*`` against pgvector < 0.8 aborts the query
    with an unrecognized-parameter error, so absence of evidence disables).
    """
    version = parse_pgvector_version(extversion)
    if version is None:
        return False
    return version >= MIN_ITERATIVE_SCAN_VERSION

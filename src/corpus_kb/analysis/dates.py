"""Document date provenance (WP-1): resolve, lift, and backfill.

One nullable ``documents.document_date`` column (migration 027) feeds the
temporal topic-trends build. This module owns the three date seams:

* :func:`resolve_document_date` — the guarded parse of one metadata
  mapping (``published_time``, then ``modified_time``).
* :func:`plan_document_date` — the forward-ingest lift: the earliest
  declared date across a plan's chunk metadata (web ``published_time``
  first; pptx core-properties ``created`` first).
* :func:`run_document_date_backfill` — the tenant-scoped, idempotent
  backfill; one SET-based UPDATE implementing exactly the migration 027
  rule (per document, the earliest of published_time, then modified_time,
  then the chunk's ``source_timestamp``).

Every parse is regex-guarded: a value must start ``YYYY-MM-DD`` AND
:func:`datetime.fromisoformat` must accept the whole string. Anything
else (``"coming soon"``, month 13, trailing prose) resolves to None.
Absence stays honest — a malformed string never fails a write. A naive
value is read as UTC, so a deck saved without a zone and a page stamped
``+02:00`` compare against one clock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import asyncpg

from ..storage.tenant_conn import tenant_connection

BACKFILL_RULE_VERSION: Final[int] = 1

# The declared-date vocabulary, in preference order: the web keys first
# (web/metadata.py), then the pptx core-properties keys (_pptx_parts.py).
_DOC_DATE_KEYS: Final[tuple[str, ...]] = ("published_time", "modified_time")
_PPTX_DATE_KEYS: Final[tuple[str, ...]] = ("created", "modified")

_ISO_PREFIX: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_date_value(value: object) -> datetime | None:
    """One guarded ISO-8601 parse; None on absent, non-text, or malformed."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not _ISO_PREFIX.match(text):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def resolve_document_date(metadata: Mapping[str, object]) -> datetime | None:
    """The declared date in one metadata mapping: published, then modified."""
    for key in _DOC_DATE_KEYS:
        parsed = parse_date_value(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def plan_document_date(chunk_metadatas: Iterable[Mapping[str, object]]) -> datetime | None:
    """The document date lifted from chunk metadata; the earliest wins.

    Each chunk answers ``published_time`` then ``modified_time`` first and
    the pptx core-properties keys second. One strategy owns one document,
    so the vocabularies never mix inside one plan.
    """
    best: datetime | None = None
    for metadata in chunk_metadatas:
        parsed = resolve_document_date(metadata)
        if parsed is None:
            for key in _PPTX_DATE_KEYS:
                parsed = parse_date_value(metadata.get(key))
                if parsed is not None:
                    break
        if parsed is not None and (best is None or parsed < best):
            best = parsed
    return best


_DOCUMENT_DATE_COLUMN_SQL = """
    SELECT to_regclass('public.documents') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'documents'
             AND column_name = 'document_date'
       )
"""

# The malformed-value guard runs per value under an exception handler, so
# one bad string degrades to NULL for that candidate and the UPDATE never
# aborts (the migration 027 comment's contract). The helper is a session-
# local pg_temp function: it lives and dies with this transaction and
# touches no shared schema.
_DOCUMENT_DATE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION pg_temp.safe_doc_date(value text)
RETURNS timestamptz
LANGUAGE plpgsql
STABLE
AS $doc_date_fn$
BEGIN
    IF value IS NULL OR value !~ '^\\d{4}-\\d{2}-\\d{2}' THEN
        RETURN NULL;
    END IF;
    RETURN value::timestamptz;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END
$doc_date_fn$
"""

# One deterministic SET-based UPDATE per tenant. Preference per chunk is
# the migration 027 rule (published_time, then modified_time, then the
# chunk's own source_timestamp); per document the EARLIEST candidate wins
# (MIN). The ``IS DISTINCT FROM`` guard makes a second run a no-op: an
# already-correct date is never rewritten.
_DOCUMENT_DATE_BACKFILL_SQL = """
    WITH per_chunk AS (
        SELECT c.doc_id,
               COALESCE(
                   pg_temp.safe_doc_date(c.metadata->>'published_time'),
                   pg_temp.safe_doc_date(c.metadata->>'modified_time'),
                   c.source_timestamp
               ) AS candidate
        FROM chunks c
        WHERE c.tenant_id = $1
    ),
    per_doc AS (
        SELECT doc_id, MIN(candidate) AS best
        FROM per_chunk
        WHERE candidate IS NOT NULL
        GROUP BY doc_id
    )
    UPDATE documents d
    SET document_date = p.best
    FROM per_doc p
    WHERE d.tenant_id = $1
      AND d.doc_id = p.doc_id
      AND d.document_date IS DISTINCT FROM p.best
    RETURNING d.doc_id
"""


@dataclass(frozen=True)
class BackfillResult:
    """One backfill run's counts (the rule version rides on the envelope params)."""

    documents_scanned: int
    documents_dated: int


async def document_dates_present(pg_pool: asyncpg.Pool) -> bool:
    """True when migration 027 has run (the ``document_date`` column exists)."""
    async with pg_pool.acquire() as conn:
        return bool(await conn.fetchval(_DOCUMENT_DATE_COLUMN_SQL))


async def run_document_date_backfill(pg_pool: asyncpg.Pool, tenant_id: str) -> BackfillResult:
    """Run the tenant-scoped date backfill in ONE transaction.

    The session timezone is pinned to UTC first, so a naive candidate
    parses the same way on every host (the forward path reads naive
    values as UTC too). Returns the scanned count and the count of
    documents whose stored date this run actually changed.
    """
    async with tenant_connection(pg_pool, tenant_id) as conn:
        await conn.execute(_DOCUMENT_DATE_GUARD_SQL)
        await conn.execute("SET LOCAL timezone = 'UTC'")
        counted = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE tenant_id = $1", tenant_id
        )
        scanned = int(counted) if counted is not None else 0
        updated = await conn.fetch(_DOCUMENT_DATE_BACKFILL_SQL, tenant_id)
    return BackfillResult(documents_scanned=scanned, documents_dated=len(updated))

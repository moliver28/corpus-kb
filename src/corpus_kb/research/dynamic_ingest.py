"""Dynamic ingestion: file/dir/glob scanning with a two-key dedup (todo-11 (d)).

DEDUP KEYS ARE SEPARATE (r8):
  * file_sha256 over RAW FILE BYTES drives the `ingested_files` ledger —
    re-runs process ONLY new/changed files;
  * text_sha256 (normalized turn text) keys the content-addressed turn
    store — a changed file whose turns are unchanged still no-ops at the
    text layer.
The ledger is INGEST-OWNED operational state (mtime/size are not
event-derivable): EXCLUDED from the rebuild drop-set and lazily
reconstructed by idempotent re-scan (a missing row is simply re-ingested;
research_transcript_text dedup makes that safe).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import asyncpg

from corpus_kb.domain.transcript import normalize_turn_text, text_sha256
from corpus_kb.research.transcript_parser import (
    infer_source_type,
    parse_transcript,
)
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".txt", ".md", ".vtt", ".srt", ".csv", ".docx"}
DEFAULT_WATCH_INTERVAL_S = 10


@dataclass
class IngestDecision:
    path: Path
    file_sha256: str
    skipped: bool
    reason: str = ""
    source_type: str = ""
    text_shas: list[str] = field(default_factory=list)


async def scan_path(path: Path) -> list[Path]:
    """Resolve a file, directory, or glob pattern into candidate files."""
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(
            p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )
    parent = path.parent if path.parent.is_dir() else Path.cwd()
    return sorted(
        p for p in parent.glob(str(path)) if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def ledger_state(
    pool: asyncpg.Pool, tenant_id: UUID, path: Path
) -> tuple[str | None, datetime | None, int | None]:
    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT file_sha256, mtime, size FROM ingested_files "
            "WHERE tenant_id = $1 AND path = $2",
            str(tenant_id),
            str(path),
        )
    if row is None:
        return None, None, None
    return row["file_sha256"], row["mtime"], row["size"]


async def ledger_record(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    path: Path,
    sha: str,
    source_type: str,
    doc_id: UUID | None,
) -> None:
    stat = path.stat()
    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO ingested_files
            (tenant_id, path, mtime, size, file_sha256, source_type, doc_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (tenant_id, path) DO UPDATE SET
                mtime = EXCLUDED.mtime, size = EXCLUDED.size,
                file_sha256 = EXCLUDED.file_sha256, source_type = EXCLUDED.source_type,
                doc_id = EXCLUDED.doc_id, ingested_at = NOW()
            """,
            str(tenant_id),
            str(path),
            datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            stat.st_size,
            sha,
            source_type,
            str(doc_id) if doc_id else None,
        )


def turn_text_shas(turns_text: list[str]) -> list[str]:
    return [text_sha256(normalize_turn_text(t)) for t in turns_text]


async def plan_file(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    path: Path,
    force: bool = False,
) -> IngestDecision:
    """Decide whether a file needs (re)ingestion: file-hash ledger first."""
    sha = file_sha256(path)
    if not force:
        seen_sha, _, _ = await ledger_state(pool, tenant_id, path)
        if seen_sha == sha:
            return IngestDecision(path, sha, skipped=True, reason="file-hash unchanged")
    turns = parse_transcript(path)
    return IngestDecision(
        path,
        sha,
        skipped=False,
        source_type=infer_source_type(path, turns.turns),
        text_shas=turn_text_shas([t.text for t in turns.turns]),
    )


async def watch(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    directory: Path,
    interval_s: int = DEFAULT_WATCH_INTERVAL_S,
) -> None:
    """Tail a drop-directory forever (todo-20 --watch feed)."""
    logger.info("watching %s (interval %ds)", directory, interval_s)
    while True:
        try:
            for path in await scan_path(directory):
                decision = await plan_file(pool, tenant_id, path)
                if not decision.skipped:
                    logger.info("new/changed file detected: %s", path)
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("watch scan failed: %s", exc)
            await asyncio.sleep(interval_s)

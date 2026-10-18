"""The general-RAG demo corpus loader (UX program Wave 6).

One call ingests the bundled sample knowledge folder (``docs/demo-kb``)
through the standard ingest tool layer — the SAME ``ingest_file`` entry
the MCP/HTTP/CLI surfaces use, so the demo cannot drift from the real
pipeline. The research transcript pipeline is NOT involved: these are
markdown documents on the default strategy.

The corpus resolves REPO-ROOT-RELATIVE (``src/corpus_kb`` is two levels
below the checkout root), like ``research/demo.py``'s bundled corpus.
An installed wheel has no repository, so the loader raises a typed
``DemoCorpusMissingError`` whose remediation names the folder — an
honest miss, never a silent no-op.

Receipt envelope (the house shape, one meaning per key):

- ``status``: ``success`` when every file ingested or skipped unchanged,
  ``partial`` when any file failed.
- ``result.ingested``: files that completed this call.
- ``result.skipped``: files the ingest short-circuit reported unchanged.
- ``result.chunk_counts``: chunk count per ingested file (skipped files
  were not re-chunked, so they carry no count).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import asyncpg

from corpus_kb.errors import DependencyMissingError
from corpus_kb.tools.ingest_common import load_config_or_pass
from corpus_kb.tools.ingest_tools import ingest_file

logger = logging.getLogger(__name__)

# src/corpus_kb/demo_kb.py -> parents: [corpus_kb, src, repo root].
DEMO_KB_ROOT = Path(__file__).resolve().parents[2] / "docs" / "demo-kb"

DEMO_KB_FOLDER_HINT = "docs/demo-kb"


class DemoCorpusMissingError(DependencyMissingError):
    """The bundled general-RAG demo corpus folder is absent."""

    remediation = (
        "The sample knowledge folder ships with the repository checkout "
        f"({DEMO_KB_FOLDER_HINT}). Run from a clone, or ingest your own files "
        "with corpus-kb ingest."
    )


async def load_demo_kb(
    config: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Ingest every demo corpus file; return the receipt envelope.

    When ``pool`` is None the loader opens one from the configured DSN
    and closes it before returning (the CLI path). The UI passes the
    server's pool, which stays open. Per-file failures flip the status
    to ``partial`` and land in the message — the loader never raises
    for a bad file, only for a missing corpus folder.
    """
    files = _demo_files()
    cfg = load_config_or_pass(config)
    owned_pool = pool is None
    active_pool = pool if pool is not None else await _open_pool(cfg)
    ingested: list[str] = []
    skipped: list[str] = []
    chunk_counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    try:
        for path in files:
            name = path.name
            result = await ingest_file(str(path), active_pool, cfg)
            status = str(result.get("status", "error"))
            if status == "success":
                ingested.append(name)
                chunk_counts[name] = int(cast("int", result.get("chunk_count", 0)))
            elif status == "skipped":
                skipped.append(name)
            else:
                message = str(result.get("message", "unknown ingest failure"))
                failures[name] = message
                logger.warning("Demo corpus file %s failed: %s", name, message)
    finally:
        if owned_pool:
            await active_pool.close()
    return _envelope(ingested, skipped, chunk_counts, failures)


def _demo_files() -> list[Path]:
    """The demo markdown files, or the typed missing-corpus error."""
    if not DEMO_KB_ROOT.is_dir():
        raise DemoCorpusMissingError(f"The sample knowledge folder is missing: {DEMO_KB_ROOT}")
    files = sorted(DEMO_KB_ROOT.glob("*.md"))
    if not files:
        raise DemoCorpusMissingError(
            f"The sample knowledge folder holds no markdown files: {DEMO_KB_ROOT}"
        )
    return files


async def _open_pool(cfg: dict[str, Any]) -> asyncpg.Pool:
    """One pool for a CLI-shaped call (closed by the caller's finally)."""
    from corpus_kb.server_wiring import initialize_postgres_pool

    db_cfg = cast(dict[str, Any], cfg.get("database", {}) or {})
    dsn = str(db_cfg.get("connection_string", ""))
    return await initialize_postgres_pool(dsn)


def _envelope(
    ingested: list[str],
    skipped: list[str],
    chunk_counts: dict[str, int],
    failures: dict[str, str],
) -> dict[str, Any]:
    """The house receipt: status, result, and an honest failure message."""
    status = "success" if not failures else "partial"
    envelope: dict[str, Any] = {
        "status": status,
        "result": {
            "ingested": ingested,
            "skipped": skipped,
            "chunk_counts": chunk_counts,
        },
    }
    if failures:
        details = "; ".join(f"{name}: {message}" for name, message in failures.items())
        envelope["message"] = f"Some sample files failed to ingest. {details}"
    return envelope


__all__ = ["DEMO_KB_FOLDER_HINT", "DEMO_KB_ROOT", "DemoCorpusMissingError", "load_demo_kb"]

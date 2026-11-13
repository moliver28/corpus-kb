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

Receipt envelope (the house shape, one meaning per key; the top-level
keys stay exactly ``status`` and ``result`` on every run):

- ``status``: ``success`` when every file ingested or skipped unchanged,
  ``partial`` when any file failed. A file stored WITHOUT real vectors
  (the embedder was unreachable) still counts as ingested: the chunks
  are in the store and text search finds them (issue #414).
- ``result.ingested``: files that completed this call.
- ``result.skipped``: files the ingest short-circuit reported unchanged.
- ``result.chunk_counts``: chunk count per ingested file (skipped files
  were not re-chunked, so they carry no count).
- ``result.degraded``: ``true`` when one or more ingested files carry no
  usable vectors. The key appears only on a degraded run.
- ``result.failures``: per-file failure text from the ingest receipt,
  sorted by file name (partial runs only; no extra top-level key).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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
    to ``partial`` and land in ``result.failures`` — the loader never
    raises for a bad file, only for a missing corpus folder.

    A ``partial`` ingest receipt is a failure only when nothing reached
    the store. With the chunks stored, the file is a degraded success.
    """
    files = _demo_files()
    cfg = load_config_or_pass(config)
    owned_pool = pool is None
    active_pool = pool if pool is not None else await _open_pool(cfg)
    ingested: list[str] = []
    skipped: list[str] = []
    chunk_counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    degraded = False
    try:
        for path in files:
            name = path.name
            result = await ingest_file(str(path), active_pool, cfg)
            status = str(result.get("status", "error"))
            stored = status == "partial" and _stored_chunks(result) > 0
            if status == "success" or stored:
                ingested.append(name)
                chunk_counts[name] = _as_int(result.get("chunk_count"))
                if stored:
                    degraded = True
                    logger.warning(
                        "Demo corpus file %s loaded in degraded mode: %s",
                        name,
                        _failure_text(result),
                    )
            elif status == "skipped":
                skipped.append(name)
            else:
                message = _failure_text(result)
                failures[name] = message
                logger.warning("Demo corpus file %s failed: %s", name, message)
    finally:
        if owned_pool:
            await active_pool.close()
    return _envelope(ingested, skipped, chunk_counts, failures, degraded=degraded)


def _as_int(value: object) -> int:
    """A receipt count as an int (0 for a missing or non-integer value)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _stored_chunks(result: dict[str, object]) -> int:
    """Chunks the store holds for this file (0 when the write did not land).

    ``chunk_count`` is the in-memory count, so it cannot prove a write.
    ``pg_chunk_total`` is the stored count of the whole document. A sink
    without that count answers 0; this run's write count is the fallback.
    """
    total = _as_int(result.get("pg_chunk_total"))
    return total if total > 0 else _as_int(result.get("pg_chunk_count"))


def _failure_text(result: dict[str, object]) -> str:
    """The receipt's own failure words: its ``errors``, else its ``message``.

    A ``partial`` receipt carries ``errors`` and no ``message``; an
    ``error`` receipt carries ``message``. Reading only ``message`` hid
    the real cause of every degraded run (issue #414).
    """
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors)
    return str(result.get("message", "unknown ingest failure"))


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
    """One store for a CLI-shaped call (closed by the caller's finally).

    The declared type stays ``asyncpg.Pool`` for the pipeline signatures;
    a duckdb config yields the gateway at runtime (issue #330).
    """
    from corpus_kb.storage.connect import config_conn_str, open_pool_typed_store

    return await open_pool_typed_store(config_conn_str(cfg))


def _envelope(
    ingested: list[str],
    skipped: list[str],
    chunk_counts: dict[str, int],
    failures: dict[str, str],
    degraded: bool = False,
) -> dict[str, Any]:
    """The house receipt: status plus one result object (fixed shape).

    Failure detail lives in ``result.failures``, never on the top level,
    so the envelope's key set is the same on success and partial runs.
    ``degraded`` rides the result and appears only on a degraded run,
    like ``failures``: a clean run keeps the pre-#414 key set.
    """
    status = "success" if not failures else "partial"
    result: dict[str, Any] = {
        "ingested": ingested,
        "skipped": skipped,
        "chunk_counts": chunk_counts,
    }
    if degraded:
        result["degraded"] = True
    if failures:
        result["failures"] = {name: failures[name] for name in sorted(failures)}
    return {"status": status, "result": result}


__all__ = ["DEMO_KB_FOLDER_HINT", "DEMO_KB_ROOT", "DemoCorpusMissingError", "load_demo_kb"]

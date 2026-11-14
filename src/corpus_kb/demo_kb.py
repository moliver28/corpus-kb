"""The general-RAG demo corpus loader (UX program Wave 6).

One call ingests the bundled sample knowledge folder (``docs/demo-kb``)
through the standard ingest tool layer — the SAME ``ingest_file`` entry
the MCP/HTTP/CLI surfaces use, so the demo cannot drift from the real
pipeline. The research transcript pipeline is NOT involved: these are
markdown documents on the default strategy.

The corpus resolves in both install layouts: the copy shipped inside
the package (``corpus_kb/demo_corpus``, wheel and pipx installs) first,
then the checkout folder (``docs/demo-kb``). When both are absent the
loader raises a typed ``DemoCorpusMissingError`` whose remediation
names the folder — an honest miss, never a silent no-op. A lockstep
test keeps the two copies equal (tests/test_packaging_assets.py).

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

import importlib.resources
import logging
from pathlib import Path
from typing import Any

import asyncpg

from corpus_kb.contracts.receipts import failure_text, stored_chunk_count
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
        "The sample knowledge folder ships inside the package and with the "
        f"repository checkout ({DEMO_KB_FOLDER_HINT}). Reinstall corpus-kb, "
        "or run from a clone, or ingest your own files with corpus-kb ingest."
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
            stored = status == "partial" and stored_chunk_count(result) > 0
            if status == "success" or stored:
                ingested.append(name)
                chunk_counts[name] = _as_int(result.get("chunk_count"))
                if stored:
                    degraded = True
                    logger.warning(
                        "Demo corpus file %s loaded in degraded mode: %s",
                        name,
                        failure_text(result),
                    )
            elif status == "skipped":
                skipped.append(name)
            else:
                message = failure_text(result)
                failures[name] = message
                logger.warning("Demo corpus file %s failed: %s", name, message)
    finally:
        if owned_pool:
            await active_pool.close()
    return _envelope(ingested, skipped, chunk_counts, failures, degraded=degraded)


def _as_int(value: object) -> int:
    """A receipt count as an int (0 for a missing or non-integer value)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _packaged_corpus_root() -> Path | None:
    """The corpus copy shipped inside the package, or None.

    Wheels are filesystem layouts, so the traversable path is a real
    directory path.
    """
    ref = importlib.resources.files("corpus_kb") / "demo_corpus"
    if not ref.is_dir():
        return None
    return Path(str(ref))


def _demo_files() -> list[Path]:
    """The demo markdown files, or the typed missing-corpus error."""
    root = _packaged_corpus_root() or DEMO_KB_ROOT
    if not root.is_dir():
        raise DemoCorpusMissingError(f"The sample knowledge folder is missing: {root}")
    files = sorted(root.glob("*.md"))
    if not files:
        raise DemoCorpusMissingError(f"The sample knowledge folder holds no markdown files: {root}")
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

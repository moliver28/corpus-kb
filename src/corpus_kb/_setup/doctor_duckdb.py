"""DuckDB-side checks for ``corpus-kb doctor`` (storage portability §9).

Pure diagnostics in the research-check contract (name/status/detail/fix/
doc; never flip the doctor exit code). The section assembly lives in
``doctor_storage.py``; the individual checks here are importable and
testable on their own.

* :func:`duckdb_lock_check` — the single-writer file lock, with the
  likely holder class named in plain words.
* :func:`fts_watermark_check` — the lexical-index COUNT watermark the
  search path enforces (``storage/duckdb_fts.py``); the doctor reports,
  never rebuilds.
* :func:`extension_load_check` — ``LOAD`` probes for fts/vss/postgres on
  a scratch connection; failure prints the offline-vendoring fix
  (auto-install stays off by program decision).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from corpus_kb._setup.doctor_research import (
    STATUS_FAIL,
    STATUS_INFO,
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)
from corpus_kb.storage.duckdb_support import is_duckdb_lock_error

if TYPE_CHECKING:  # function-local at runtime (program rule 9)
    import duckdb

DOC_ADMIN = "docs/ADMIN.md"
DOC_INSTALL = "docs/INSTALL.md"

#: The one-command fix for most findings in this section.
DUCKDB_SETUP_FIX = "corpus-kb setup --backend duckdb"

#: (extension, required) — probed with LOAD on a scratch connection.
EXTENSION_PROBES: tuple[tuple[str, bool], ...] = (
    ("fts", True),
    ("vss", False),
    ("postgres", False),
)

#: Prebuilt LOAD statements (probe names are repo constants).
_PROBE_LOAD_SQL: dict[str, str] = {
    "fts": "LOAD 'fts'",
    "vss": "LOAD 'vss'",
    "postgres": "LOAD 'postgres'",
}

_EXTENSION_PURPOSE = {
    "fts": "Lexical search needs it.",
    "vss": "Optional: the experimental HNSW index.",
    "postgres": "Optional: the migrate-data Postgres ATTACH.",
}

_CHUNKS_COUNT_SQL = "SELECT count(*) FROM chunks"
_FTS_DOCS_SQL = "SELECT count(*) FROM fts_main_chunks.docs"


def _default_opener(path: str) -> None:
    """Open read-write and close: the single-writer lock probe itself."""
    import duckdb

    con = duckdb.connect(path)
    con.close()


def duckdb_lock_check(path: str, opener: Callable[[str], None] | None = None) -> ResearchCheck:
    """One short open names the lock state and its likely holder class."""
    name = "DuckDB file lock"
    if not Path(path).exists():
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"No DuckDB file at {path}.",
            fix=DUCKDB_SETUP_FIX,
            doc=DOC_INSTALL,
        )
    try:
        (opener or _default_opener)(path)
    except Exception as exc:
        if is_duckdb_lock_error(exc):
            return ResearchCheck(
                name=name,
                status=STATUS_FAIL,
                detail=(
                    "The file is locked. One process at a time may open a "
                    "DuckDB file. A running Corpus-KB server, or another "
                    "DuckDB program, holds this file."
                ),
                fix="Stop the other program. Then run corpus-kb doctor again.",
                doc=DOC_ADMIN,
            )
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail=f"Cannot open {path}: {exc}",
            fix="Check the file is a DuckDB database.",
            doc=DOC_INSTALL,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_OK,
        detail="writable; not locked by another process",
        doc=DOC_ADMIN,
    )


def fts_watermark_check(con: duckdb.DuckDBPyConnection) -> ResearchCheck:
    """Report the chunks-vs-index-docs COUNT watermark; never rebuild here."""
    import duckdb

    name = "FTS index watermark"
    try:
        total_row = con.execute(_CHUNKS_COUNT_SQL).fetchone()
        total = int(total_row[0]) if total_row else 0
    except duckdb.CatalogException:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail="chunks table missing. The migrations never ran on this file.",
            fix=DUCKDB_SETUP_FIX,
            doc=DOC_INSTALL,
        )
    try:
        docs_row = con.execute(_FTS_DOCS_SQL).fetchone()
        indexed = int(docs_row[0]) if docs_row else 0
    except duckdb.CatalogException:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail="lexical index missing. The first search builds it.",
            fix=f"Run any search to build it. Or pre-install the extension: {DUCKDB_SETUP_FIX}.",
            doc=DOC_INSTALL,
        )
    if indexed == total:
        return ResearchCheck(
            name=name,
            status=STATUS_OK,
            detail=f"lexical index current ({total} chunks)",
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_WARN,
        detail=f"lexical index is stale (index rows {indexed}, chunks {total}).",
        fix="Run any search. The index rebuilds before the first query.",
        doc=DOC_ADMIN,
    )


def _vendoring_fix(name: str) -> str:
    """The offline-vendoring remediation (auto-install stays off)."""
    return (
        f"Online: run {DUCKDB_SETUP_FIX}; it downloads the extension once. "
        f"Offline: on an online machine run INSTALL {name} with duckdb. "
        "Copy the extension file into the folder set as extension_directory. "
        "Auto-install stays off."
    )


def extension_load_check(
    probe_con: Any, probes: tuple[tuple[str, bool], ...] = EXTENSION_PROBES
) -> list[ResearchCheck]:
    """LOAD each extension on a scratch connection; required vs optional."""
    checks: list[ResearchCheck] = []
    for name, required in probes:
        try:
            probe_con.execute(_PROBE_LOAD_SQL[name])
        except Exception:
            checks.append(
                ResearchCheck(
                    name=f"duckdb extension {name}",
                    status=STATUS_WARN if required else STATUS_INFO,
                    detail=f"did not load. {_EXTENSION_PURPOSE[name]}",
                    fix=_vendoring_fix(name),
                    doc=DOC_INSTALL,
                )
            )
        else:
            checks.append(
                ResearchCheck(
                    name=f"duckdb extension {name}",
                    status=STATUS_OK,
                    detail="loads",
                    doc=DOC_INSTALL,
                )
            )
    return checks


__all__ = [
    "DUCKDB_SETUP_FIX",
    "EXTENSION_PROBES",
    "duckdb_lock_check",
    "extension_load_check",
    "fts_watermark_check",
]

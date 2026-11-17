"""DuckDB driver-error translation onto the shared taxonomy (issue #455 split).

Moved verbatim out of ``duckdb_store`` so the store's line ceiling funds
the entity-id alias resolution; the store re-imports the name, so the
error-boundary import path (``duckdb_store._translate_error``) and the
raw-class rows pinned in ``tests/test_error_taxonomy.py`` stay put.
"""

from __future__ import annotations

from ..errors import DatabaseUnavailableError, MigrationMissingError


def _translate_error(exc: Exception) -> Exception:
    """Map DuckDB failures onto the shared taxonomy (no new error classes)."""
    import duckdb

    text = str(exc).lower()
    if isinstance(exc, duckdb.IOException) or "lock" in text or "conflict" in text:
        return DatabaseUnavailableError(
            "Another program is using this database file. Close it, then try again.",
            remediation="Close the other program, then run the command again.",
        )
    if isinstance(exc, duckdb.CatalogException):
        return MigrationMissingError(
            f"Required table or graph object is missing: {exc}",
            remediation="Run corpus-kb setup. Then try again.",
        )
    return exc

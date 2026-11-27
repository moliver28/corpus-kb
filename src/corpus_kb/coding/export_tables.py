"""Codebook CSV tables (R9, TD-16): the canonical flat-text export.

``coding_export`` writes six tables under ``coding.export_dir`` when the
key is set: codes, keywords, theory, versions, releases, and a data
dictionary. Rules (AR-1/AR-2/AR-6/AR-11): UTF-8 with LF endings, RFC 4180
quoting via the stdlib writer, an empty field for null, ``true``/``false``
for booleans, RFC 3339 timestamps, stable text or UUID identifiers, and a
SHA-256 digest per file in the envelope. Rows order by their keys, so the
bytes are deterministic.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, missing_table_error, tenant_scope

logger = logging.getLogger(__name__)

__all__ = ["write_codebook_tables"]

#: The config leaf this module reads (config-backend-parity reader token).
EXPORT_DIR_KEY = "coding.export_dir"


def _cell(value: object) -> str:
    """One CSV cell: RFC 3339 timestamps, true/false booleans, empty nulls."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return stamp.isoformat()
    return str(value)


async def _fetch(
    pool: CodingStore, tenant_id: UUID, sql: str
) -> tuple[tuple[str, ...], list[list[str]]]:
    """One tenant-scoped table fetch; answers (header, rows) of strings."""
    async with tenant_scope(pool, tenant_id) as conn:
        rows = await conn.fetch(sql, str(tenant_id))
    if not rows:
        return (), []
    first = rows[0]
    keys = tuple(map(str, first.keys()))
    return keys, [[_cell(row[k]) for k in keys] for row in rows]


_TABLES: tuple[tuple[str, str], ...] = (
    (
        "codes.csv",
        "SELECT code_id, name, brief_definition, inclusion_criteria, "
        "exclusion_criteria, actor_scope, is_provisional, created_at "
        "FROM code_registry WHERE tenant_id=$1 ORDER BY code_id",
    ),
    (
        "keywords.csv",
        "SELECT keyword, code_id, kind, source, status FROM code_keywords "
        "WHERE tenant_id=$1 ORDER BY code_id, kind, keyword",
    ),
    (
        "versions.csv",
        "SELECT version_id, label, sha256, paradigm, created_at FROM codebook_versions "
        "WHERE tenant_id=$1 ORDER BY version_id",
    ),
    (
        "releases.csv",
        "SELECT release_id, codebook_id, codebook_version, state, profile, "
        "created_at, released_at FROM codebook_releases WHERE tenant_id=$1 ORDER BY release_id",
    ),
)

_THEORY_HEADER: tuple[str, ...] = ("code_id", "tradition", "construct", "citation", "note")

_DATA_DICTIONARY: tuple[tuple[str, str, str], ...] = (
    (
        "codes",
        "columns",
        "code_id, name, brief_definition, inclusion_criteria, "
        "exclusion_criteria, actor_scope, is_provisional, created_at",
    ),
    (
        "keywords",
        "columns",
        "keyword, code_id, kind (inclusion|exclusion), source, status (active|retired)",
    ),
    ("theory", "columns", "code_id, theory (a JSON object: tradition, construct, citation, note)"),
    ("versions", "columns", "version_id, label, sha256, paradigm, created_at"),
    (
        "releases",
        "columns",
        "release_id, codebook_id, codebook_version, state, profile, created_at, released_at",
    ),
    ("all", "null", "An empty field means null or not applicable."),
    ("all", "booleans", "true and false, lowercase."),
    ("all", "timestamps", "RFC 3339 UTC."),
    (
        "all",
        "missing",
        "A row absent from a table means the state does not exist; no sentinel value is used.",
    ),
)


def _csv_bytes(header: tuple[str, ...], rows: list[list[str]]) -> bytes:
    """One table's canonical bytes (LF endings, UTF-8, minimal quoting)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    if header:
        writer.writerow(header)
        writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _theory_cell(row: Any) -> dict[str, str]:
    """Flatten one theory JSON object into a row of strings.

    The stored theory arrives as a dict (jsonb) or a JSON string (the
    DuckDB mirror); both flatten the same way.
    """
    theory = row["theory"]
    if isinstance(theory, str):
        try:
            theory = json.loads(theory)
        except ValueError:
            theory = {}
    parsed: dict[str, Any] = theory if isinstance(theory, dict) else {}
    return {
        "code_id": _cell(row["code_id"]),
        "tradition": _cell(parsed.get("tradition")),
        "construct": _cell(parsed.get("construct")),
        "citation": _cell(parsed.get("citation")),
        "note": _cell(parsed.get("note")),
    }


async def _theory_rows(pool: CodingStore, tenant_id: UUID) -> list[list[str]]:
    """The theory table, flattened from the stored JSON per code."""
    async with tenant_scope(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT code_id, theory FROM code_registry WHERE tenant_id=$1 ORDER BY code_id",
            str(tenant_id),
        )
    return [[_theory_cell(row)[key] for key in _THEORY_HEADER] for row in rows]


async def write_codebook_tables(
    pool: CodingStore, tenant_id: UUID, export_dir: str
) -> dict[str, Any]:
    """Write the six codebook CSV tables under ``export_dir``.

    An empty ``export_dir`` answers ``status: disabled`` (the envelope key
    stays present). A store without the coding tables answers
    ``status: unavailable`` and writes nothing.
    """
    if not export_dir:
        return {
            "status": "disabled",
            "reason": f"set {EXPORT_DIR_KEY} in config.yaml to write the CSV tables",
        }
    try:
        tables: list[tuple[str, bytes, int]] = []
        for name, sql in _TABLES:
            header, rows = await _fetch(pool, tenant_id, sql)
            tables.append((name, _csv_bytes(header, rows), (1 if header else 0) + len(rows)))
        theory_rows = await _theory_rows(pool, tenant_id)
        tables.append(("theory.csv", _csv_bytes(_THEORY_HEADER, theory_rows), 1 + len(theory_rows)))
        tables.append(
            (
                "data_dictionary.csv",
                _csv_bytes(("table", "column", "convention"), [list(r) for r in _DATA_DICTIONARY]),
                1 + len(_DATA_DICTIONARY),
            )
        )
    except Exception as exc:
        if not missing_table_error(exc):
            raise
        logger.warning("Codebook CSV export degraded: the coding tables are absent", exc_info=True)
        return {
            "status": "unavailable",
            "reason": "coding tables absent (run corpus-kb setup)",
        }
    target = Path(export_dir)
    target.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, object]] = {}
    for name, payload, n_rows in tables:
        path = target / name
        path.write_bytes(payload)
        files[name] = {"rows": n_rows, "sha256": hashlib.sha256(payload).hexdigest()}
    return {
        "status": "written",
        "dir": str(target),
        "files": dict(sorted(files.items())),
        "n_tables": len(files),
    }

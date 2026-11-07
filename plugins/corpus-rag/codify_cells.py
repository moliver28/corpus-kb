"""The ``codify cells`` builder: the next cells file for one coder (issue #354).

Read-only SQL through the study server answers three questions: the
latest codebook version, the (chunk, code) candidates this coder has
not coded yet, and how many chunks carry no pool signals. The file
holds at most five candidate codes per cell, best score first, capped
at ``--limit`` chunks. Unpooled chunks cannot be coded (they have no
candidates); the command reports how many and points at ``codify pool``.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import codify_common

CODER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_LIMIT = 100
MAX_CODES_PER_CELL = 5

LATEST_VERSION_SQL = (
    "SELECT version_id FROM codebook_versions "
    "ORDER BY created_at DESC, version_id DESC LIMIT 1"
)

UNPOOLED_SQL = (
    "SELECT COUNT(*) AS n FROM chunks c WHERE NOT EXISTS ("
    "SELECT 1 FROM chunk_signals s "
    "WHERE s.chunk_id = c.chunk_id AND s.tenant_id = c.tenant_id)"
)


def latest_codebook_version(study: dict) -> str:
    """The newest codebook version id, or a typed error that says what to do."""
    rows = codify_common.sql(study, LATEST_VERSION_SQL)
    if not rows or not rows[0].get("version_id"):
        raise codify_common.CodifyError(
            1,
            "No codebook is loaded in this study yet. Load one first: "
            "corpus-rag codify load-codebook <file.json> -p <slug>",
        )
    return str(rows[0]["version_id"])


def candidate_sql(version_id: str, coder: str) -> str:
    """Uncoded (chunk, code) pairs for this coder, best score first."""
    return (
        "SELECT s.chunk_id, s.code_id, MAX(s.score) AS top_score "
        "FROM chunk_signals s "
        "WHERE s.code_id IN (SELECT code_id FROM code_registry "
        f"WHERE codebook_version_id = '{version_id}') "
        "AND NOT EXISTS (SELECT 1 FROM chunk_codes cc "
        "WHERE cc.chunk_id = s.chunk_id AND cc.code_id = s.code_id "
        f"AND cc.coder = '{coder}' AND cc.tenant_id = s.tenant_id) "
        "GROUP BY s.chunk_id, s.code_id "
        "ORDER BY s.chunk_id ASC, top_score DESC, s.code_id ASC"
    )


def build_cells_file(
    slug: str, study: dict, coder: str, limit: int | None, out: str
) -> int:
    """Build and write the cells file; the caller has already turned the server up."""
    if not CODER_RE.fullmatch(coder or ""):
        raise codify_common.CodifyError(
            1,
            f"Coding coder id '{coder}' is not usable. Use letters, numbers, "
            "dots, hyphens, or underscores.",
        )
    version = latest_codebook_version(study)
    rows = codify_common.sql(study, candidate_sql(version, coder))
    grouped: dict[str, list[str]] = {}
    for row in rows:
        chunk_id = str(row.get("chunk_id"))
        grouped.setdefault(chunk_id, []).append(str(row.get("code_id")))
    cap = limit if isinstance(limit, int) and limit > 0 else DEFAULT_LIMIT
    cells = [
        {"chunk_id": chunk_id, "code_ids": codes[:MAX_CODES_PER_CELL]}
        for chunk_id, codes in list(grouped.items())[:cap]
    ]
    unpooled = _unpooled_count(study)
    out_path = Path(out or "cells.json").expanduser()
    document = {
        "cells": cells,
        "coder": coder,
        "codebook_version_id": version,
        "study": slug,
    }
    out_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    if not cells:
        print(f"ok: no cells to code for coder '{coder}' in '{slug}' ({out_path}).")
    else:
        print(
            f"ok: wrote {len(cells)} cells for coder '{coder}' to {out_path} "
            f"(codebook version {version}; at most {MAX_CODES_PER_CELL} "
            "candidate codes per cell)."
        )
    if unpooled:
        print(
            f"{unpooled} chunk(s) have no pool signals, so they cannot be "
            f"coded yet. Run `corpus-rag codify pool -p {slug}`, then build cells again."
        )
    if cells:
        print(f"Next: corpus-rag codify code --cells {out_path} -p {slug} --coder {coder}")
    return 0


def _unpooled_count(study: dict) -> int:
    """Chunks with no pool signals; zero when the read fails or says nothing."""
    rows = codify_common.sql(study, UNPOOLED_SQL)
    if not rows:
        return 0
    try:
        return int(rows[0].get("n") or 0)
    except (TypeError, ValueError):
        return 0

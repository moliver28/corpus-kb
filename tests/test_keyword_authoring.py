"""Authored keyword rows (TD-10): loader writes, source column, duplicate refusal.

Runs on the DuckDB gateway (the offline mirror of the coding schema), so the
one-active-inclusion-keyword-per-tenant index is real when the refusal fires.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from _coding_vectors import _embed

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.coding.codebook import CodebookError
from corpus_kb.coding.codebook_loader import load_codebook
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")


def _codebook(include_by_code: dict[str, list[str]]) -> dict[str, object]:
    categories = []
    for code_id, include in include_by_code.items():
        categories.append(
            {
                "id": code_id,
                "name": f"name-{code_id}",
                "brief_definition": f"definition {code_id}",
                "inclusion_criteria": f"criteria {code_id}",
                "exclusion_criteria": f"not {code_id}",
                "examples": [],
                "theory": {},
                "keywords": {"include": include, "exclude": ["never"]},
            }
        )
    return {"paradigm": "codebook", "categories": categories}


async def _gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / f"auth-{uuid4().hex}.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _keyword_rows(gw: DuckDbGateway, code_id: str) -> list[tuple[str, str, str]]:
    rows = await gw.fetch(
        "SELECT keyword, kind, source, status FROM code_keywords"
        " WHERE tenant_id=$1 AND code_id=$2 ORDER BY keyword, kind",
        str(TENANT),
        code_id,
    )
    return [(r["keyword"], r["kind"], f"{r['source']}:{r['status']}") for r in rows]


async def test_loader_writes_authored_keyword_rows(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    doc = _codebook({"C1": ["safety"], "C2": ["layoffs"]})
    await load_codebook(doc, gw, str(TENANT), _embed)
    rows_c1 = await _keyword_rows(gw, "C1")
    assert ("safety", "inclusion", "codebook:active") in rows_c1
    assert ("never", "exclusion", "codebook:active") in rows_c1
    rows_c2 = await _keyword_rows(gw, "C2")
    assert ("layoffs", "inclusion", "codebook:active") in rows_c2
    await gw.close()


async def test_duplicate_include_term_across_codes_is_refused(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    doc = _codebook({"C1": ["safety"], "C2": ["safety"]})
    with pytest.raises(CodebookError, match=r"C1.*C2|C2.*C1"):
        await load_codebook(doc, gw, str(TENANT), _embed)
    await gw.close()


async def test_duplicate_include_against_earlier_load_is_refused(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    await load_codebook(_codebook({"C1": ["safety"]}), gw, str(TENANT), _embed)
    doc2 = _codebook({"C3": ["Safety"]})  # case-insensitive clash with C1's active row
    with pytest.raises(CodebookError, match="already active"):
        await load_codebook(doc2, gw, str(TENANT), _embed)
    await gw.close()


async def test_same_codebook_resubmit_is_idempotent(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    doc = _codebook({"C1": ["safety"]})
    await load_codebook(doc, gw, str(TENANT), _embed)
    await load_codebook(doc, gw, str(TENANT), _embed)  # no raise, no dup rows
    rows = await _keyword_rows(gw, "C1")
    assert rows.count(("safety", "inclusion", "codebook:active")) == 1
    await gw.close()

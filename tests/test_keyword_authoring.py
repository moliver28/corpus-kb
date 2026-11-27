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


async def test_reload_replaces_earlier_load_authored_rows(tmp_path: Path) -> None:
    """T1 pin (athena row 47): a new version's authored keywords supersede.

    The one-active-inclusion index forces replace semantics on reload, so
    an authored term an earlier load claimed does NOT refuse a new load —
    the earlier active row retires first (refine D-1, retire-on-reload).
    Same-document duplicates still refuse (the claims check, next test).
    """
    gw = await _gateway(tmp_path)
    await load_codebook(_codebook({"C1": ["safety"]}), gw, str(TENANT), _embed)
    doc2 = _codebook({"C3": ["Safety"]})  # case-insensitive clash with C1's active row
    await load_codebook(doc2, gw, str(TENANT), _embed)  # no raise: reload replaces
    rows_c1 = await _keyword_rows(gw, "C1")
    assert ("safety", "inclusion", "codebook:retired") in rows_c1
    rows_c3 = await _keyword_rows(gw, "C3")
    assert ("Safety", "inclusion", "codebook:active") in rows_c3
    # keywords.csv exports the full history with its status column, so only
    # the current-version rows read as active (refine D-1 third check).
    from corpus_kb.coding.export_tables import write_codebook_tables

    out = tmp_path / "csv"
    result = await write_codebook_tables(gw, TENANT, str(out))
    assert result["status"] == "written"
    keywords_csv = (out / "keywords.csv").read_text(encoding="utf-8").splitlines()
    assert "safety,C1,inclusion,codebook,retired" in keywords_csv
    assert "Safety,C3,inclusion,codebook,active" in keywords_csv
    await gw.close()


async def test_synthesized_active_rows_still_refuse_authored_claims(tmp_path: Path) -> None:
    """Only authored rows retire on reload; synthesized claims still refuse."""
    gw = await _gateway(tmp_path)
    await gw.execute(
        "INSERT INTO code_keywords (keyword, code_id, tenant_id, kind, source, status)"
        " VALUES ('safety', 'SYN-1', $1, 'inclusion', 'synthesis', 'active')"
        " ON CONFLICT DO NOTHING",
        str(TENANT),
    )
    doc = _codebook({"C1": ["safety"]})
    with pytest.raises(CodebookError, match="already active"):
        await load_codebook(doc, gw, str(TENANT), _embed)
    await gw.close()


async def test_same_codebook_resubmit_is_idempotent(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    doc = _codebook({"C1": ["safety"]})
    await load_codebook(doc, gw, str(TENANT), _embed)
    await load_codebook(doc, gw, str(TENANT), _embed)  # no raise, no dup rows
    rows = await _keyword_rows(gw, "C1")
    assert rows.count(("safety", "inclusion", "codebook:active")) == 1
    await gw.close()

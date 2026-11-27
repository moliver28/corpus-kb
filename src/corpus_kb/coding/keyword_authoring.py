"""Authored keyword rows: one codebook load's writes and conflict refusals.

The loader (codebook_loader.py) stays inside the 250-line gate, so the
keyword-writing half of phase 3 lives here (TD-10). Authored rows carry
``source = 'codebook'`` and coexist with synthesized rows, separated by
the existing ``code_keywords.source`` column (athena G-12).
"""

from __future__ import annotations

from typing import Any

from corpus_kb.coding._scope import ScopedConn
from corpus_kb.coding.codebook import CodebookError

__all__ = ["write_codebook_keywords"]


async def write_codebook_keywords(
    conn: ScopedConn, tenant: str, embedded: list[tuple[dict[str, Any], str, str]]
) -> None:
    """Write authored keywords as active ``codebook``-source rows (TD-10).

    Refuses a duplicate active include term across codes with a typed
    CodebookError naming the term and both codes: the one-active-inclusion-
    keyword-per-tenant index (migration 012) makes the conflict real
    (athena G-11). Exclusion rows carry no such constraint. Idempotent via
    the table's primary key. Called inside the loader's write transaction.
    """
    claims: dict[str, str] = {}
    for code, _dv, _pv in embedded:
        keywords = code.get("keywords")
        if not isinstance(keywords, dict):
            continue
        code_id = str(code["code_id"] if "code_id" in code else code.get("id"))
        for kind, key in (("inclusion", "include"), ("exclusion", "exclude")):
            for term in keywords.get(key) or []:
                if kind == "inclusion":
                    await _refuse_duplicate_include(conn, tenant, str(term), code_id, claims)
                    claims[str(term).lower()] = code_id
                await conn.execute(
                    "INSERT INTO code_keywords (keyword, code_id, tenant_id, kind, source, status)"
                    " VALUES ($1, $2, $3, $4, 'codebook', 'active') ON CONFLICT DO NOTHING",
                    term,
                    code_id,
                    tenant,
                    kind,
                )


async def _refuse_duplicate_include(
    conn: ScopedConn, tenant: str, term: str, code_id: str, claims: dict[str, str]
) -> None:
    """Raise when this active include term is already claimed by another code.

    Two checks: a term this same document already gave another code, and a
    term an earlier load left active under a different code.
    """
    claimed = claims.get(term.lower())
    if claimed is not None and claimed != code_id:
        raise CodebookError(
            f"Include keyword {term!r} is claimed by codes {claimed!r} and {code_id!r}"
        )
    row = await conn.fetchrow(
        "SELECT code_id FROM code_keywords WHERE tenant_id=$1"
        " AND kind='inclusion' AND status='active' AND lower(keyword)=lower($2)",
        tenant,
        term,
    )
    if row is not None and str(row["code_id"]) != code_id:
        raise CodebookError(
            f"Include keyword {term!r} is already active for code "
            f"{str(row['code_id'])!r}; refused for code {code_id!r}"
        )

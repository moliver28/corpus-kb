"""The DuckDB delete cascade (issue #330).

DuckDB cannot declare ON DELETE actions and checks foreign keys against
the last committed state, so :func:`storage.duckdb_cascade.
delete_document_cascade` owns the rules in code: one committed stage per
doomed table, children first, the documents row last, and a leaves-first
pass loop for the self-referencing ``shadow_chunks`` tree. These tests
run on a real migrated file, per the issue's test plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_cascade import delete_document_cascade
from corpus_kb.storage.gateway import DuckDbGateway

if TYPE_CHECKING:
    from pathlib import Path

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


@pytest.fixture()
async def migrated_gateway(duckdb_path: Path) -> Any:
    """A migrated DuckDB file behind the gateway."""
    run_duckdb_migrations(f"duckdb:///{duckdb_path.as_posix()}")
    gateway = await DuckDbGateway.connect(f"duckdb:///{duckdb_path.as_posix()}")
    yield gateway
    await gateway.close()


async def _seed_graph(gateway: Any, tenant: str) -> str:
    """One document with the full doomed neighborhood; returns its id."""
    doc, chunk, ent1, ent2, rel = (str(uuid4()) for _ in range(5))
    spk, stmt, conflict, tag = (str(uuid4()) for _ in range(4))
    sc_parent, sc_child = str(uuid4()), str(uuid4())
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        doc,
        tenant,
        "delete-cascade-seed",
        "transcript",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5)",
        chunk,
        tenant,
        doc,
        "seed text",
        "transcript",
    )
    await gateway.execute(
        "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, embedding_model)"
        " VALUES ($1::uuid, $2, $3::FLOAT[2560], $4)",
        chunk,
        tenant,
        [0.1] * 2560,
        "nomic-embed-text",
    )
    await gateway.execute(
        "INSERT INTO entities (entity_id, tenant_id, name, entity_type, source_document_id)"
        " VALUES ($1::uuid, $2, $3, $4, $5::uuid)",
        ent1,
        tenant,
        f"cascade-entity-{tenant}",
        "concept",
        doc,
    )
    await gateway.execute(
        "INSERT INTO entities (entity_id, tenant_id, name, entity_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        ent2,
        tenant,
        f"survivor-{tenant}",
        "concept",
    )
    await gateway.execute(
        "INSERT INTO relations (relation_id, tenant_id, source_entity_id, target_entity_id,"
        " relation_type, chunk_id) VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5, $6::uuid)",
        rel,
        tenant,
        ent1,
        ent2,
        "related_to",
        chunk,
    )
    await gateway.execute(
        "INSERT INTO research_speakers (speaker_id, tenant_id, doc_id, raw_label)"
        " VALUES ($1::uuid, $2, $3::uuid, $4)",
        spk,
        tenant,
        doc,
        "S1",
    )
    await gateway.execute(
        "INSERT INTO research_units (unit_id, tenant_id, doc_id, speaker_id, seq, text,"
        " text_sha256) VALUES (nextval('research_units_unit_id_seq'), $1, $2::uuid,"
        " $3::uuid, 0, $4, $5)",
        tenant,
        doc,
        spk,
        "unit",
        "a" * 64,
    )
    await gateway.execute(
        "INSERT INTO statements (statement_id, tenant_id, chunk_id, document_id,"
        " statement_hash, text, template_id, template_version, entities, metadata)"
        " VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5, $6, $7, $8, '[]', '{}')",
        stmt,
        tenant,
        chunk,
        doc,
        "b" * 64,
        "claim",
        "t",
        "v1",
    )
    await gateway.execute(
        "INSERT INTO statement_conflicts (conflict_id, tenant_id, statement_a_id,"
        " statement_b_id, anchor_name, kind, gate_reached, verdict, metadata)"
        " VALUES ($1::uuid, $2, $3::uuid, $3::uuid, $4, $5, $6, $7, '{}')",
        conflict,
        tenant,
        stmt,
        "anchor",
        "entity_swap",
        "A",
        "conflict",
    )
    await gateway.execute(
        "INSERT INTO tags (tag_id, tenant_id, name) VALUES ($1::uuid, $2, $3)", tag, tenant, "t1"
    )
    await gateway.execute(
        "INSERT INTO document_tags (doc_id, tag_id, tenant_id) VALUES ($1::uuid, $2::uuid, $3)",
        doc,
        tag,
        tenant,
    )
    await gateway.execute(
        "INSERT INTO shadow_chunks (shadow_chunk_id, tenant_id, doc_id, level, seq, text,"
        " token_count) VALUES ($1::uuid, $2, $3::uuid, $4, 0, $5, 10)",
        sc_parent,
        tenant,
        doc,
        "parent",
        "parent text",
    )
    await gateway.execute(
        "INSERT INTO shadow_chunks (shadow_chunk_id, tenant_id, doc_id, level, seq, text,"
        " token_count, parent_id) VALUES ($1::uuid, $2, $3::uuid, $4, 1, $5, 10, $6::uuid)",
        sc_child,
        tenant,
        doc,
        "child",
        "child text",
        sc_parent,
    )
    return doc


async def test_cascade_deletes_the_doomed_set_and_keeps_survivors(
    migrated_gateway: Any,
) -> None:
    """Children die (deepest first for shadow_chunks); entities/relations stay.

    The SET NULL links are nulled, the sibling tenant's rows are untouched,
    and the documents row is gone at the end.
    """
    gateway = migrated_gateway
    doc = await _seed_graph(gateway, TENANT)
    # A sibling tenant owns an identical-shape row set: none of it moves.
    other_doc = await _seed_graph(gateway, OTHER_TENANT)

    counts = await delete_document_cascade(gateway, TENANT, doc)

    assert counts["documents"] == 1
    assert counts["chunks"] == 1
    assert counts["chunks_vectors"] == 1
    assert counts["statements"] == 1
    assert counts["statement_conflicts"] == 1
    assert counts["research_speakers"] == 1
    assert counts["research_units"] == 1
    assert counts["shadow_chunks"] == 2  # leaves-first pass covered both levels
    # Survivors: the entity rows stay (one link nulled), the relation row
    # stays with its chunk link nulled.
    assert await gateway.fetchval("SELECT count(*) FROM entities WHERE tenant_id = $1", TENANT) == 2
    assert (
        await gateway.fetchval(
            "SELECT count(*) FROM entities WHERE tenant_id = $1 AND"
            " source_document_id IS NULL AND name LIKE 'cascade-entity-%'",
            TENANT,
        )
        == 1
    )
    assert (
        await gateway.fetchval(
            "SELECT count(*) FROM relations WHERE tenant_id = $1 AND chunk_id IS NULL", TENANT
        )
        == 1
    )
    assert (
        await gateway.fetchval("SELECT count(*) FROM documents WHERE tenant_id = $1", TENANT) == 0
    )
    # The sibling tenant's graph is intact.
    assert (
        await gateway.fetchval("SELECT count(*) FROM documents WHERE tenant_id = $1", OTHER_TENANT)
        == 1
    )
    assert (
        await gateway.fetchval(
            "SELECT count(*) FROM chunks WHERE doc_id::uuid = $1::uuid", other_doc
        )
        == 1
    )


async def test_cascade_is_idempotent(migrated_gateway: Any) -> None:
    """A second run answers all-zero counts and changes nothing."""
    gateway = migrated_gateway
    doc = await _seed_graph(gateway, TENANT)
    await delete_document_cascade(gateway, TENANT, doc)
    second = await delete_document_cascade(gateway, TENANT, doc)
    assert not any(second.values()), {k: v for k, v in second.items() if v}


async def test_delete_tool_routes_the_cascade_on_duckdb(
    duckdb_path: Path, migrated_gateway: Any
) -> None:
    """``delete_document`` (the ONE delete implementation) cascades on DuckDB."""
    from corpus_kb.tools.ingest_tools import delete_document

    gateway = migrated_gateway
    doc = await _seed_graph(gateway, TENANT)
    result = await delete_document(doc, gateway, tenant_id=TENANT)
    assert result["status"] == "success"
    assert (
        await gateway.fetchval("SELECT count(*) FROM statements WHERE tenant_id = $1", TENANT) == 0
    )


async def test_second_040_run_over_live_rows_preserves_data(
    duckdb_path: Path,
) -> None:
    """Re-running the 041 rebuild SQL over live rows keeps every value.

    The migration runner applies each file once; this test executes the
    rebuild's statement list a second time by hand over rows already in
    the tables (the issue's "second run of 040 over live rows").
    """
    from importlib import resources

    path = f"duckdb:///{duckdb_path.as_posix()}"
    run_duckdb_migrations(path)
    gateway = await DuckDbGateway.connect(path)
    doc = await _seed_graph(gateway, TENANT)
    await gateway.close()

    sql = (
        resources.files("corpus_kb") / "migrations_duckdb" / "041_entities_doc_link_no_fk.sql"
    ).read_text(encoding="utf-8")
    import duckdb

    con = duckdb.connect(str(duckdb_path))
    try:
        con.execute(sql)  # the driver runs the file's statements in sequence
        rows = con.execute("SELECT count(*) FROM entities WHERE tenant_id = ?", [TENANT]).fetchone()
        assert rows is not None and rows[0] == 2
        rel = con.execute("SELECT count(*) FROM relations WHERE tenant_id = ?", [TENANT]).fetchone()
        assert rel is not None and rel[0] == 1
        fk = con.execute(
            "SELECT count(*) FROM duckdb_constraints()"
            " WHERE table_name = 'entities' AND constraint_type = 'FOREIGN KEY'"
        ).fetchone()
        assert fk is not None and fk[0] == 0
        doc_left = con.execute(
            "SELECT count(*) FROM documents WHERE doc_id::uuid = ?", [doc]
        ).fetchone()
        assert doc_left is not None and doc_left[0] == 1
    finally:
        con.close()

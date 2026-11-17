"""DuckDB graph id space: every CLI-returned entity id resolves (issue #455).

The entities read model dedupes on (tenant_id, name, entity_type) and the
FIRST projected aggregate uuid becomes the canonical row id. A later
``add-entity`` for the same name returns a NEW aggregate uuid that is not
in the table, so a relation built from it violated the relations FK and
landed in the DLQ. The projection now aliases every later aggregate id to
the canonical row, the relations projection resolves endpoints through
the aliases, and the DuckDB graph store resolves alias ids on reads — so
search and hops read the same model the CLI writes against.

Hermetic: one tmp ``.duckdb`` file, the eventsourcing sqlite sidecar,
no Ollama. The flow mirrors the CLI: ``add-entity`` (aggregate) ->
DocumentsProjection catch-up -> ``add-relation`` -> catch-up.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.domain.application import events_table_name
from corpus_kb.domain.models import AddEntityCommand, AddRelationCommand
from corpus_kb.handlers.command_handler import CommandHandler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.storage.duckdb_support import Row

TENANT = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def gateway(duckdb_path: Path):
    dsn = f"duckdb:///{duckdb_path.as_posix()}"
    run_duckdb_migrations(dsn)
    gw = await DuckDbGateway.connect(dsn)
    yield gw
    await gw.close()


@pytest.fixture(autouse=True)
def _close_event_apps():
    from corpus_kb.domain.application import reset_app

    yield
    reset_app()


@pytest.fixture
def command_handler(gateway: DuckDbGateway, duckdb_path: Path) -> CommandHandler:
    """CommandHandler wired like production: duckdb event sidecar + gateway."""
    return CommandHandler(
        {
            "database": {
                "backend": "duckdb",
                "connection_string": f"duckdb:///{duckdb_path.as_posix()}",
            }
        },
        pool=None,
        gateway=gateway,
    )


async def _project_pending(gateway: DuckDbGateway, handler: CommandHandler) -> None:
    """Advance the documents projection over the pending events (the one drain)."""
    app = handler.app
    reader = EventReader(gateway, app.mapper, events_table_name(app), recorder=app.recorder)
    projection = DocumentsProjection(gateway, CheckpointManager(gateway), DLQHandler(gateway))
    await projection.catch_up(reader, TENANT)


async def _unresolved_dlq(gateway: DuckDbGateway) -> list[Row]:
    return await gateway.fetch("SELECT event_type FROM projection_dlq WHERE resolved = FALSE")


async def test_cli_flow_relation_from_returned_id_materializes_without_dlq(
    gateway: DuckDbGateway, command_handler: CommandHandler
) -> None:
    """The issue's exact flow: relation built from a CLI-returned id lands."""
    first = command_handler.handle_add_entity(
        AddEntityCommand(tenant_id=TENANT, name="alpha", entity_type="tool")
    )
    await _project_pending(gateway, command_handler)
    second = command_handler.handle_add_entity(
        AddEntityCommand(tenant_id=TENANT, name="alpha", entity_type="tool")
    )
    assert second["entity_id"] != first["entity_id"]
    await _project_pending(gateway, command_handler)

    beta = command_handler.handle_add_entity(
        AddEntityCommand(tenant_id=TENANT, name="beta", entity_type="tool")
    )
    await _project_pending(gateway, command_handler)

    command_handler.handle_add_relation(
        AddRelationCommand(
            tenant_id=TENANT,
            source_entity_id=UUID(str(second["entity_id"])),
            target_entity_id=UUID(str(beta["entity_id"])),
            relation_type="uses",
        )
    )
    await _project_pending(gateway, command_handler)

    rows = await gateway.fetch("SELECT source_entity_id, target_entity_id FROM relations")
    assert len(rows) == 1, "the relation must materialize"
    entity_rows = await gateway.fetch("SELECT entity_id FROM entities")
    canonical_ids = {str(row["entity_id"]) for row in entity_rows}
    assert canonical_ids == {str(first["entity_id"]), str(beta["entity_id"])}
    source = str(rows[0]["source_entity_id"])
    target = str(rows[0]["target_entity_id"])
    assert source in canonical_ids, "relation endpoints must sit in the entity table"
    assert target in canonical_ids
    assert await _unresolved_dlq(gateway) == [], "no event may land in the DLQ"


async def test_old_store_rows_heal_through_new_events(
    gateway: DuckDbGateway, command_handler: CommandHandler
) -> None:
    """A store advanced before the fix (row under the old id, no alias) heals.

    The next Entity.Created for the same name aliases its aggregate id to
    the existing canonical row, and relations from the new id satisfy the
    FK — no rebuild needed for the id space itself.
    """
    old_id = str(uuid4())
    await gateway.execute(
        "INSERT INTO entities (entity_id, tenant_id, name, entity_type) VALUES (?, ?, ?, ?)",
        old_id,
        str(TENANT),
        "qwen",
        "model",
    )
    created = command_handler.handle_add_entity(
        AddEntityCommand(tenant_id=TENANT, name="qwen", entity_type="model")
    )
    assert created["entity_id"] != old_id
    await _project_pending(gateway, command_handler)

    aliases = await gateway.fetch("SELECT alias_entity_id, entity_id FROM entity_id_aliases")
    assert len(aliases) == 1, "the new aggregate id aliases to the old row"
    assert str(aliases[0]["alias_entity_id"]) == str(created["entity_id"])
    assert str(aliases[0]["entity_id"]) == old_id

    command_handler.handle_add_relation(
        AddRelationCommand(
            tenant_id=TENANT,
            source_entity_id=UUID(str(created["entity_id"])),
            target_entity_id=UUID(old_id),
            relation_type="related_to",
        )
    )
    await _project_pending(gateway, command_handler)
    rows = await gateway.fetch("SELECT source_entity_id, target_entity_id FROM relations")
    assert len(rows) == 1, "the relation from the new id must materialize"
    assert str(rows[0]["source_entity_id"]) == old_id
    assert await _unresolved_dlq(gateway) == []


async def test_graph_reads_resolve_alias_ids(gateway: DuckDbGateway) -> None:
    """get_entity / relations / hops / bfs accept an alias id (same model)."""
    canonical = str(uuid4())
    beta = str(uuid4())
    alias = str(uuid4())
    await gateway.execute(
        "INSERT INTO entities (entity_id, tenant_id, name, entity_type) VALUES (?, ?, ?, ?)",
        canonical,
        str(TENANT),
        "qwen",
        "model",
    )
    await gateway.execute(
        "INSERT INTO entities (entity_id, tenant_id, name, entity_type) VALUES (?, ?, ?, ?)",
        beta,
        str(TENANT),
        "beta",
        "tool",
    )
    await gateway.execute(
        "INSERT INTO entity_id_aliases (tenant_id, alias_entity_id, entity_id) VALUES (?, ?, ?)",
        str(TENANT),
        alias,
        canonical,
    )
    await gateway.execute(
        "INSERT INTO relations (relation_id, tenant_id, source_entity_id,"
        " target_entity_id, relation_type) VALUES (?, ?, ?, ?, ?)",
        str(uuid4()),
        str(TENANT),
        canonical,
        beta,
        "ships",
    )

    store = DuckDbGraphStore(gateway.duplicate_connection(), tenant_id=str(TENANT))
    try:
        entity = await store.get_entity(alias)
        assert entity is not None, "an alias id must resolve to its canonical row"
        assert entity.name == "qwen"

        relations = await store.get_entity_relations(alias)
        assert len(relations) == 1, "relations of the canonical row answer the alias"
        assert relations[0].target_entity_id == beta

        neighbors = await store.neighbors(alias, 1, "out")
        assert [row["entity_id"] for row in neighbors] == [beta]

        visited = await store.bfs(alias, 2)
        assert set(visited["visited"]) == {canonical, beta}
    finally:
        await store.close()

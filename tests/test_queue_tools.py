"""The ingest queue (textbook program W1): store, handlers, config, ontology.

Offline pins for the queue wave:

* migrations 037/038 — table, indexes, RLS, and the rollback pairs.
* ``IngestQueueStore`` / ``queue_tools`` — content-hash idempotency, the
  cancel state rules, and the pinned status payload (DB stubbed at the
  recording-connection seams tests/test_ingest_runs_project.py uses).
* config — the new textbook / queue / graph_policy / graph keys parse,
  and the validate_configs enums refuse junk (direct module import, the
  test_validate_configs.py shape).
* ontology — the optional relation ``graph_eligible`` flag parses and
  defaults to eligible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from corpus_kb._setup import validate_configs
from corpus_kb.config import load_config
from corpus_kb.errors import ConflictError, NotFoundError, ValidationError
from corpus_kb.ontology import load_ontology
from corpus_kb.queue.store import IngestQueueStore
from corpus_kb.tools.queue_payload import queue_entry_payload
from corpus_kb.tools.queue_tools import content_hash_for, queue_cancel, queue_enqueue, queue_status

TENANT = "00000000-0000-0000-0000-000000000003"
REPO = Path(__file__).resolve().parent.parent
MIGRATION_PG = REPO / "src" / "corpus_kb" / "migrations" / "037_ingest_queue.sql"
MIGRATION_DUCK = REPO / "src" / "corpus_kb" / "migrations_duckdb" / "038_ingest_queue.sql"
PACKAGED_ONTOLOGY = REPO / "src" / "corpus_kb" / "config" / "ontology.yaml"

QUEUE_ID = "00000000-0000-0000-0000-0000000000e1"
AT = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _row(status: str = "pending", **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": QUEUE_ID,
        "tenant_id": TENANT,
        "path": "/tmp/book.pdf",
        "title": None,
        "project_id": None,
        "profile": None,
        "strategy_config": "{}",
        "status": status,
        "attempts": 0,
        "max_attempts": 3,
        "last_error": None,
        "run_id": None,
        "content_hash": "hash-1",
        "claimed_by": None,
        "heartbeat_at": None,
        "queued_at": AT,
        "started_at": None,
        "finished_at": None,
        "updated_at": None,
    }
    row.update(over)
    return row


class _QueueConn:
    """Connection double: routes ingest_queue probes to canned rows."""

    def __init__(
        self,
        *,
        active: dict[str, Any] | None = None,
        inserted: dict[str, Any] | None = None,
        cancel: dict[str, Any] | None = None,
        single: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.active = active
        self.inserted = inserted
        self.cancel = cancel
        self.single = single
        self.rows = rows or []
        self.insert_args: tuple[object, ...] = ()
        self.last_list_sql = ""

    def transaction(self) -> _QueueConn:
        return self

    async def __aenter__(self) -> _QueueConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, Any] | None:
        if "INSERT INTO ingest_queue" in sql:
            self.insert_args = args
            return self.inserted
        if "UPDATE ingest_queue" in sql:
            return self.cancel
        return self.active if "content_hash = $2" in sql else self.single

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.last_list_sql = sql
        return self.rows


class _QueuePool:
    def __init__(self, conn: _QueueConn) -> None:
        self._conn = conn

    def acquire(self) -> _QueuePool:
        return self

    async def __aenter__(self) -> _QueueConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# Migration contract pins (test_ingest_runs_project.py shape)
# ---------------------------------------------------------------------------


def test_migration_037_pins_the_queue_table_and_indexes() -> None:
    pg = MIGRATION_PG.read_text(encoding="utf-8").lower()
    assert "create table if not exists ingest_queue" in pg
    for column in (
        "path text not null",
        "project_id uuid",
        "strategy_config jsonb not null default '{}'",
        "status text not null default 'pending'",
        "run_id uuid",
        "content_hash text not null",
        "max_attempts integer not null default 3",
    ):
        assert column in pg, column
    assert "in ('pending','running','done','failed','cancelled')" in pg
    assert "create unique index if not exists idx_ingest_queue_active_hash" in pg
    assert "where status in ('pending', 'running')" in pg
    assert "idx_ingest_queue_claim" in pg and "on ingest_queue(status, queued_at)" in pg
    assert "force row level security" in pg


def test_migration_038_mirrors_with_duckdb_types() -> None:
    duck = MIGRATION_DUCK.read_text(encoding="utf-8").lower()
    assert "id varchar primary key" in duck
    assert "strategy_config json not null default '{}'" in duck
    assert "run_id varchar" in duck
    # No partial predicate on DuckDB's unique index (022 mirror pattern)
    # and no RLS; the index statement itself carries no WHERE clause.
    assert (
        "create unique index if not exists idx_ingest_queue_active_hash\n"
        "    on ingest_queue(tenant_id, content_hash);" in duck
    )
    assert "row level security" not in duck


def test_migration_rollbacks_drop_the_queue() -> None:
    for path in (
        REPO / "src" / "corpus_kb" / "migrations" / "rollback_037_ingest_queue.sql",
        REPO / "src" / "corpus_kb" / "migrations_duckdb" / "rollback_038_ingest_queue.sql",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "drop table if exists ingest_queue" in text
        assert "drop index if exists idx_ingest_queue_active_hash" in text


# ---------------------------------------------------------------------------
# Content-hash idempotency
# ---------------------------------------------------------------------------


def test_content_hash_is_stable_across_key_order() -> None:
    first = content_hash_for("a.pdf", "p", {"k": 1, "z": 2})
    second = content_hash_for("a.pdf", "p", {"z": 2, "k": 1})
    assert first == second
    assert first != content_hash_for("a.pdf", "p", {"k": 2})
    assert first != content_hash_for("a.pdf", None, {"k": 1})


def test_content_hash_covers_the_absolute_path() -> None:
    assert content_hash_for("x.pdf", None, None) == content_hash_for("x.pdf", "", {})
    assert content_hash_for("x.pdf", None, None) != content_hash_for("y.pdf", None, None)


@pytest.mark.asyncio
async def test_enqueue_answers_the_active_twin() -> None:
    conn = _QueueConn(active=_row())
    store = IngestQueueStore(_QueuePool(conn), TENANT)
    row, deduplicated = await store.enqueue(
        path="/tmp/book.pdf",
        title=None,
        project_id=None,
        profile=None,
        strategy_config={},
        max_attempts=3,
        content_hash="hash-1",
    )
    assert deduplicated is True
    assert row["id"] == QUEUE_ID


@pytest.mark.asyncio
async def test_enqueue_inserts_when_no_active_twin_exists() -> None:
    conn = _QueueConn(active=None, inserted=_row())
    store = IngestQueueStore(_QueuePool(conn), TENANT)
    row, deduplicated = await store.enqueue(
        path="/tmp/book.pdf",
        title=None,
        project_id=None,
        profile=None,
        strategy_config={},
        max_attempts=3,
        content_hash="hash-1",
    )
    assert deduplicated is False
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_enqueue_requires_a_path() -> None:
    with pytest.raises(ValidationError):
        await queue_enqueue(_QueuePool(_QueueConn()), path="   ")


@pytest.mark.asyncio
async def test_enqueue_rejects_a_non_object_strategy_config() -> None:
    with pytest.raises(ValidationError):
        await queue_enqueue(_QueuePool(_QueueConn()), path="book.pdf", strategy_config="nope")


# ---------------------------------------------------------------------------
# Status reads and the pinned payload
# ---------------------------------------------------------------------------


def test_entry_payload_keys_are_pinned() -> None:
    entry = queue_entry_payload(_row())
    assert set(entry) == {
        "id",
        "path",
        "title",
        "status",
        "attempts",
        "max_attempts",
        "last_error",
        "elapsed_seconds",
        "queued_at",
        "run_id",
        "project_id",
        "pages_done",
        "pages_total",
        "chunks",
    }
    assert entry["queued_at"] == AT.isoformat()
    assert entry["pages_done"] is None and entry["chunks"] is None


def test_entry_payload_elapsed_for_a_finished_row() -> None:
    entry = queue_entry_payload(
        _row(started_at=AT, finished_at=datetime(2026, 10, 28, 12, 0, 5, tzinfo=UTC))
    )
    assert entry["elapsed_seconds"] == 5.0


@pytest.mark.asyncio
async def test_status_reads_one_row_by_id() -> None:
    report = await queue_status(_QueuePool(_QueueConn(single=_row())), queue_id=QUEUE_ID)
    assert report["status"] == "success"
    entries = report["entries"]
    assert isinstance(entries, list) and entries[0]["id"] == QUEUE_ID
    assert report["count"] == 1


@pytest.mark.asyncio
async def test_status_rejects_an_unknown_id() -> None:
    with pytest.raises(NotFoundError):
        await queue_status(_QueuePool(_QueueConn(single=None)), queue_id="nope")


@pytest.mark.asyncio
async def test_status_lists_active_rows_deterministically() -> None:
    conn = _QueueConn(rows=[_row(), _row(status="running")])
    report = await queue_status(_QueuePool(conn), limit=10)
    assert report["count"] == 2
    # Deterministic order with the id tiebreaker (zero-drift rule 9).
    assert "ORDER BY queued_at, id" in conn.last_list_sql


@pytest.mark.asyncio
async def test_status_rejects_a_bad_limit() -> None:
    with pytest.raises(ValidationError):
        await queue_status(_QueuePool(_QueueConn()), limit=0)


# ---------------------------------------------------------------------------
# Cancel state rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_succeeds_from_pending() -> None:
    result = await queue_cancel(
        _QueuePool(_QueueConn(cancel=_row(status="cancelled"))), queue_id=QUEUE_ID
    )
    assert result == {"status": "success", "queue_id": QUEUE_ID, "state": "cancelled"}


@pytest.mark.asyncio
async def test_cancel_refuses_a_done_row() -> None:
    conn = _QueueConn(cancel=None, single=_row(status="done"))
    with pytest.raises(ConflictError):
        await queue_cancel(_QueuePool(conn), queue_id=QUEUE_ID)


@pytest.mark.asyncio
async def test_cancel_refuses_an_already_cancelled_row() -> None:
    conn = _QueueConn(cancel=None, single=_row(status="cancelled"))
    with pytest.raises(ConflictError):
        await queue_cancel(_QueuePool(conn), queue_id=QUEUE_ID)


@pytest.mark.asyncio
async def test_cancel_rejects_an_unknown_id() -> None:
    with pytest.raises(NotFoundError):
        await queue_cancel(_QueuePool(_QueueConn()), queue_id="ghost")


@pytest.mark.asyncio
async def test_cancel_requires_an_id() -> None:
    with pytest.raises(ValidationError):
        await queue_cancel(_QueuePool(_QueueConn()), queue_id="")


# ---------------------------------------------------------------------------
# Config: the Wave 1 keys
# ---------------------------------------------------------------------------


def test_defaults_carry_the_wave_one_keys() -> None:
    cfg = load_config()
    # Subset, not equality: the textbook block grows strategy keys (window
    # subprocess tuning); the wave-one keys must stay regardless.
    assert {
        "min_pages": 120,
        "window_pages": 50,
        "parser": "docling",
        "cache_dir": "~/.corpus-kb/textbook-cache",
    }.items() <= cfg["textbook"].items()
    assert cfg["queue"] == {
        "autorun": False,
        "poll_seconds": 20,
        "stale_lease_seconds": 600,
        "max_attempts": 3,
    }
    assert cfg["graph_policy"] == {"mode": "all"}
    assert cfg["graph"]["duckdb_graph_backend"] == "cte"


def _shipped_config() -> dict[str, Any]:
    cfg = validate_configs.load_yaml(REPO / "src" / "corpus_kb" / "config.yaml")
    assert validate_configs.validate_runtime_config(cfg) == []
    return cfg


def test_shipped_yamls_carry_the_wave_one_keys() -> None:
    for name in ("config.yaml", "src/corpus_kb/config.yaml"):
        cfg = validate_configs.load_yaml(REPO / name)
        assert validate_configs.validate_runtime_config(cfg) == [], name
        assert cfg["queue"]["autorun"] is False, name


def test_textbook_enum_and_range_rules() -> None:
    cfg = _shipped_config()
    cfg["textbook"]["parser"] = "ocr"
    errors = validate_configs.validate_runtime_config(cfg)
    assert any("textbook.parser" in e and "docling" in e for e in errors)
    cfg = _shipped_config()
    cfg["textbook"]["min_pages"] = 0
    assert any("textbook.min_pages" in e for e in validate_configs.validate_runtime_config(cfg))


def test_graph_policy_mode_enum() -> None:
    cfg = _shipped_config()
    assert validate_configs.validate_runtime_config(cfg) == []
    cfg["graph_policy"]["mode"] = "none"
    errors = validate_configs.validate_runtime_config(cfg)
    assert any("graph_policy.mode" in e and "semantic_only" in e for e in errors)


def test_duckdb_graph_backend_enum() -> None:
    cfg = _shipped_config()
    cfg["graph"]["duckdb_graph_backend"] = "recursive"
    errors = validate_configs.validate_runtime_config(cfg)
    assert any("graph.duckdb_graph_backend" in e and "duckpgq" in e for e in errors)


def test_queue_knobs_reject_junk() -> None:
    cfg = _shipped_config()
    cfg["queue"]["poll_seconds"] = True
    cfg["queue"]["max_attempts"] = 0
    errors = validate_configs.validate_runtime_config(cfg)
    assert any("queue.poll_seconds" in e for e in errors)
    assert any("queue.max_attempts" in e for e in errors)


@pytest.mark.asyncio
async def test_queue_max_attempts_knob_reaches_the_insert() -> None:
    conn = _QueueConn(active=None, inserted=_row(max_attempts=7))
    report = await queue_enqueue(_QueuePool(conn), config={"queue": {"max_attempts": 7}}, path="b")
    assert report["status"] == "queued"
    assert conn.insert_args[6] == 7  # the $7 max_attempts bind


# ---------------------------------------------------------------------------
# Ontology: the optional graph_eligible flag
# ---------------------------------------------------------------------------


def test_packaged_ontology_flags_are_explicit_only() -> None:
    ontology = load_ontology(PACKAGED_ONTOLOGY)
    assert ontology.relation_graph_eligible == {"SUPPORTS": True}
    assert ontology.relation_is_graph_eligible("SUPPORTS") is True
    # Absent = eligible: the "all" policy reads today's behavior unchanged.
    assert ontology.relation_is_graph_eligible("MENTIONS") is True


def test_plain_string_ontology_defaults_to_eligible(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\nrelation_types:\n  - RELATED_TO\n", encoding="utf-8"
    )
    ontology = load_ontology(path)
    assert ontology.relation_graph_eligible == {}
    assert ontology.relation_is_graph_eligible("RELATED_TO") is True


def test_ineligible_flag_parses_and_answers(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\n"
        "relation_types:\n  - RELATED_TO\n  - {name: CITES, graph_eligible: false}\n",
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    assert ontology.relation_graph_eligible == {"CITES": False}
    assert ontology.relation_is_graph_eligible("CITES") is False


def test_flag_for_an_undeclared_relation_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\n"
        "relation_types:\n  - RELATED_TO\n"
        "relation_graph_eligible:\n  GHOST: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not declared"):
        load_ontology(path)


def test_non_boolean_flag_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\nrelation_types:\n  - {name: CITES, graph_eligible: often}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Malformed ontology file"):
        load_ontology(path)

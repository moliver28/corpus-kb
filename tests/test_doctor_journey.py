"""Doctor journey checks (novice-UX program W3).

The pure matrix tests build small synthetic registers (the register model
allows id subsets). The state-reader tests use a stub asyncpg-shaped
connection and a real throwaway DuckDB file, so nothing here touches a
live Postgres. Wiring tests fake the probe at the module seams the way
``test_cli_doctor`` does.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import duckdb
import pytest

from corpus_kb._setup import doctor_journey as dj
from corpus_kb._setup import doctor_journey_maps as djm
from corpus_kb._setup import doctor_journey_state as djs
from corpus_kb._setup.doctor_journey import (
    JourneyState,
    build_journey_checks,
    journey_checks,
)
from corpus_kb._setup.doctor_research import (
    REEMBED_FIX,
    STATUS_INFO,
    STATUS_OK,
    STATUS_SKIPPED,
    ResearchCheck,
)
from corpus_kb.analysis.method_register import INPUT_KINDS, MethodRecord, MethodRegister

# ---------------------------------------------------------------------------
# Synthetic register helpers
# ---------------------------------------------------------------------------


def _record(
    mid: str,
    *,
    op: str | None = None,
    difficulty: str | None = None,
    inputs: tuple[str, ...] = ("raw_text",),
    prerequisites: tuple[str, ...] = (),
) -> MethodRecord:
    """One minimal valid register row (subsets stay valid per the model)."""
    return MethodRecord(
        id=mid,
        name=f"Method {mid} name",
        summary=f"Method {mid} summary.",
        paradigm="supervised",
        evidence_label="direct",
        inputs_required=list(inputs),
        calibration_effort="none",
        autonomy="automated_with_audit",
        complexity_tier="baseline",
        selection_priority="next",
        selection_notes="n",
        robustness_caveat="n",
        bias_risk="n",
        efficiency_workload="n",
        output_family="labels",
        output_shape="n",
        usage_guidance="n",
        validation_required="n",
        permitted_claims="n",
        do_not_claim="n",
        implementation_status="implemented_existing" if op else "human_led",
        op=op,
        sources=["https://example.com/a"],
        difficulty=difficulty,
        prerequisites=list(prerequisites),
    )


def _register(*rows: MethodRecord) -> MethodRegister:
    return MethodRegister(
        register_version="1.0.0",
        source_document={"title": "t", "path_note": "n"},
        selection_rules=["r"],
        adjacent_candidates="n",
        methods=list(rows),
    )


REG = _register(
    _record("02", op="analyze_run", difficulty="novice"),
    _record("04", op="labeling_build", difficulty="novice"),
    _record("09", op="topics_build", difficulty="intermediate", inputs=("raw_text", "embeddings")),
    _record(
        "13",
        op="centroids_classify",
        difficulty="intermediate",
        inputs=("embeddings", "labels"),
        prerequisites=("04",),
    ),
    _record(
        "15",
        op="lexical_classify",
        difficulty="intermediate",
        inputs=("raw_text", "labels"),
        prerequisites=("04",),
    ),
    _record(
        "16", op="classification_build", difficulty="intermediate", inputs=("embeddings", "labels")
    ),
    _record(
        "20",
        op="active_learn_next",
        difficulty="advanced",
        inputs=("labels", "predictions"),
        prerequisites=("16",),
    ),
    _record("23", op="search", difficulty="novice", inputs=("raw_text", "embeddings", "llm")),
    _record("24", op="ask", difficulty="novice", inputs=("raw_text", "llm")),
    _record("27", op="sentiment_screen", difficulty="intermediate", inputs=("raw_text", "labels")),
    _record("29", op="duplicates_screen", difficulty="novice", inputs=("raw_text", "embeddings")),
    _record("30", op="outliers_rank", difficulty="novice", inputs=("raw_text", "embeddings")),
)

HEALTHY = JourneyState(
    chunks=120, vectors=120, labels=0, predictions=0, topics=0, dated_documents=6
)


def table_name_of(sql: str) -> str:
    """The table name an information_schema.columns stub query filters on."""
    for name in ("chunks_vectors", "documents"):
        if f"table_name = '{name}'" in sql or f"table_name = {name}" in sql:
            return name
    return ""


def _by_name(checks: list[ResearchCheck]) -> dict[str, ResearchCheck]:
    return {check.name: check for check in checks}


# ---------------------------------------------------------------------------
# State matrix (pure core)
# ---------------------------------------------------------------------------


def test_empty_corpus_next_is_ingest() -> None:
    empty = JourneyState(chunks=0, vectors=0, labels=0, predictions=0, topics=0, dated_documents=0)
    checks = build_journey_checks(REG, empty, ollama_ok=True)
    found = _by_name(checks)
    assert list(found) == ["journey_state", "journey_next"]
    assert found["journey_state"].status == STATUS_INFO
    assert "corpus is empty" in found["journey_state"].detail
    nxt = found["journey_next"]
    assert "Run: corpus-kb research ingest." in nxt.detail
    assert nxt.fix == "corpus-kb research ingest"
    assert nxt.status == STATUS_INFO


def test_healthy_without_labels_next_is_labeling() -> None:
    checks = build_journey_checks(REG, HEALTHY, ollama_ok=True)
    found = _by_name(checks)
    state, nxt = found["journey_state"], found["journey_next"]
    assert state.status == STATUS_OK
    assert "You have 120 chunks and 120 vectors." in state.detail
    # Runnable today: 02, 04, 09, 23, 24, 29, 30 (13/15/16/20/27 miss labels).
    assert "You can run 7 methods today." in state.detail
    assert "Method 02 name" in state.detail
    assert "novice and intermediate levels" in state.detail
    assert "Next: build labels with method 04." in nxt.detail
    assert "Run: corpus-kb labeling-build." in nxt.detail
    assert nxt.fix == "corpus-kb labeling-build"
    assert "It unlocks methods 13, 15, 16, and 27." in nxt.detail


def test_labels_built_next_is_topics() -> None:
    built = JourneyState(
        chunks=120, vectors=120, labels=5, predictions=0, topics=0, dated_documents=6
    )
    found = _by_name(build_journey_checks(REG, built, ollama_ok=True))
    nxt = found["journey_next"]
    assert "Next: build topics with method 09." in nxt.detail
    assert nxt.fix == "corpus-kb topics-build"
    # Nothing lists topics as a prerequisite, so there is no unlock line.
    assert "unlocks" not in nxt.detail


def test_labels_built_next_is_classification() -> None:
    built = JourneyState(
        chunks=120, vectors=120, labels=5, predictions=0, topics=3, dated_documents=6
    )
    found = _by_name(build_journey_checks(REG, built, ollama_ok=True))
    nxt = found["journey_next"]
    assert "Next: build predictions with method 16." in nxt.detail
    assert nxt.fix == "corpus-kb classification-build"
    assert "It unlocks methods 20." in nxt.detail


def test_all_products_built_is_terminal() -> None:
    done = JourneyState(
        chunks=120, vectors=120, labels=5, predictions=4, topics=3, dated_documents=6
    )
    found = _by_name(build_journey_checks(REG, done, ollama_ok=True))
    nxt = found["journey_next"]
    assert nxt.status == STATUS_INFO
    assert "You built every analysis artifact this version stores." in nxt.detail
    assert nxt.fix == ""


def test_missing_vectors_suggest_reembed() -> None:
    degraded = JourneyState(
        chunks=30, vectors=0, labels=0, predictions=0, topics=0, dated_documents=6
    )
    found = _by_name(build_journey_checks(REG, degraded, ollama_ok=False))
    nxt = found["journey_next"]
    assert "Next: build vectors." in nxt.detail
    assert nxt.fix == REEMBED_FIX


def test_ollama_down_excludes_llm_methods() -> None:
    checks = build_journey_checks(REG, HEALTHY, ollama_ok=False)
    state = _by_name(checks)["journey_state"]
    # 7 minus search (23) and ask (24).
    assert "You can run 5 methods today." in state.detail


def test_same_state_renders_the_same_bytes() -> None:
    one = build_journey_checks(REG, HEALTHY, ollama_ok=True)
    two = build_journey_checks(REG, HEALTHY, ollama_ok=True)
    assert json.dumps([asdict(c) for c in one], sort_keys=True) == json.dumps(
        [asdict(c) for c in two], sort_keys=True
    )


def test_details_are_short_plain_sentences() -> None:
    banned = ("just", "simply", "obviously", "etc", "please note", "in order to", "utilize")
    for state in (
        HEALTHY,
        JourneyState(chunks=0, vectors=0, labels=0, predictions=0, topics=0, dated_documents=0),
        JourneyState(chunks=30, vectors=0, labels=0, predictions=0, topics=0, dated_documents=6),
        JourneyState(chunks=120, vectors=120, labels=5, predictions=4, topics=3, dated_documents=6),
    ):
        for check in build_journey_checks(REG, state, ollama_ok=True):
            lowered = check.detail.lower()
            assert not [word for word in banned if f" {word} " in f" {lowered} ".replace(",", " ")]
            for sentence in check.detail.replace(". ", ".\n").splitlines():
                words = [w for w in sentence.split() if w not in (".",)]
                assert len(words) <= 20, sentence


# ---------------------------------------------------------------------------
# Degraded paths (IO wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_database_skips_the_journey() -> None:
    checks = await journey_checks({}, "postgresql://down", db_ok=False, ollama_ok=True)
    assert len(checks) == 1
    assert checks[0].name == "journey"
    assert checks[0].status == STATUS_SKIPPED
    assert checks[0].fix == "corpus-kb setup"


@pytest.mark.asyncio
async def test_broken_register_omits_the_journey(monkeypatch: Any) -> None:
    def boom(config: object) -> None:
        raise ValueError("bad yaml")

    monkeypatch.setattr(dj, "load_method_register", boom)
    checks = await journey_checks({}, "", db_ok=True, ollama_ok=True)
    assert len(checks) == 1
    assert checks[0].name == "journey"
    assert checks[0].status == STATUS_INFO
    assert "register" in checks[0].detail


@pytest.mark.asyncio
async def test_uninitialized_tables_omit_the_journey(monkeypatch: Any) -> None:
    async def no_tables(conn_str: str, backend: str) -> None:
        return None

    monkeypatch.setattr(dj, "read_build_state", no_tables)
    checks = await journey_checks({}, "", db_ok=True, ollama_ok=True)
    assert len(checks) == 1
    assert checks[0].status == STATUS_INFO
    assert checks[0].fix == "corpus-kb setup"


# ---------------------------------------------------------------------------
# Build-state readers (one connection, per backend)
# ---------------------------------------------------------------------------


class _StubConn:
    """asyncpg-shaped double: fetchval answers per table name in the SQL."""

    def __init__(self, counts: dict[str, int | None], columns: set[str]) -> None:
        self.counts = counts
        self.columns = columns
        self.tenant_set = False

    def transaction(self):
        return self

    async def __aenter__(self) -> _StubConn:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.tenant_set = True

    async def execute(self, sql: str, *args: object) -> str:
        return "SET"

    async def fetchval(self, sql: str, *args: object) -> int | None:
        if "to_regclass" in sql:
            for table in self.counts:
                if f"public.{table}" in sql:
                    return None if self.counts[table] is None else 1
            return None
        if "information_schema.columns" in sql:
            for column in ("chunks_vectors", "documents"):
                if table_name_of(sql) == column:
                    wanted = "degraded" if column == "chunks_vectors" else "document_date"
                    return 1 if wanted in self.columns else 0
            return 0
        # Longest table name first so "FROM chunks_vectors" never matches "chunks".
        for table, count in sorted(self.counts.items(), key=lambda kv: -len(kv[0])):
            if f"FROM {table}" in sql:
                return count
        return 0


@pytest.mark.asyncio
async def test_postgres_state_reader_counts_each_table() -> None:
    conn = _StubConn(
        counts={
            "chunks": 12,
            "chunks_vectors": 10,
            "chunk_labels": 4,
            "chunk_predictions": 2,
            "topics": 3,
            "documents": 5,
        },
        columns={"degraded", "document_date"},
    )
    state = await djs._pg_build_state(conn)
    assert state == JourneyState(
        chunks=12, vectors=10, labels=4, predictions=2, topics=3, dated_documents=5
    )
    assert conn.tenant_set  # FORCE RLS: the counts ran under the tenant GUC


@pytest.mark.asyncio
async def test_postgres_state_reader_names_missing_tables() -> None:
    conn = _StubConn(
        counts={"chunks": 1, "chunks_vectors": 1, "chunk_labels": None},
        columns=set(),
    )
    assert await djs._pg_build_state(conn) is None


def test_duckdb_state_reader_counts_each_table(tmp_path: Any) -> None:
    path = tmp_path / "kb.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE chunks (tenant_id VARCHAR)")
    con.execute("CREATE TABLE chunks_vectors (embedding FLOAT[], degraded BOOLEAN)")
    con.execute("CREATE TABLE chunk_labels (label VARCHAR)")
    con.execute("CREATE TABLE chunk_predictions (label VARCHAR)")
    con.execute("CREATE TABLE topics (label VARCHAR)")
    con.execute("CREATE TABLE documents (document_date TIMESTAMPTZ)")
    con.execute("INSERT INTO chunks VALUES ('t'), ('t')")
    con.execute("INSERT INTO chunks_vectors VALUES ([1.0], FALSE), (NULL, TRUE)")
    con.execute("INSERT INTO chunk_labels VALUES ('a')")
    con.execute("INSERT INTO topics VALUES ('t1'), ('t2')")
    con.execute("INSERT INTO documents VALUES (NULL), ('2026-01-01')")
    con.close()
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    state = djs._duckdb_build_state(duckdb_path_to_conn_string(str(path)))
    assert state == JourneyState(
        chunks=2, vectors=1, labels=1, predictions=0, topics=2, dated_documents=1
    )


def test_duckdb_state_reader_names_missing_tables(tmp_path: Any) -> None:
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    assert djs._duckdb_build_state(duckdb_path_to_conn_string(str(path))) is None


# ---------------------------------------------------------------------------
# Drift guards: the encoded maps stay in step with the live data
# ---------------------------------------------------------------------------


def test_command_table_covers_every_implemented_op() -> None:
    from corpus_kb.analysis.method_register_loader import load_method_register

    register = load_method_register({})
    missing = sorted({row.op for row in register.methods if row.op} - set(djm.OP_CLI))
    assert not missing, f"add OP_CLI entries for: {missing}"


def test_command_table_agrees_with_the_surface_registry() -> None:
    from corpus_kb.surface_registry import SURFACES

    cli_paths = {row.mcp_tool: row.cli_path for row in SURFACES if row.cli_path}
    for op, cli_path in djm.OP_CLI.items():
        declared = cli_paths.get(op)
        if declared is not None:
            assert cli_path == declared, f"{op}: table says {cli_path}, registry says {declared}"


def test_prerequisite_rows_have_build_products() -> None:
    from corpus_kb.analysis.method_register_loader import load_method_register

    register = load_method_register({})
    prereq_ids = sorted({pid for row in register.methods for pid in row.prerequisites or []})
    by_id = {row.id: row for row in register.methods}
    for pid in prereq_ids:
        row = by_id[pid]
        assert row.op in djm.BUILD_PRODUCTS, f"method {pid}: map its op to a build product"


def test_every_input_kind_is_mapped() -> None:
    mapped = set(djm.INPUT_STATE) | set(djm.UNPROBED_INPUTS) | {"llm"}
    assert mapped == set(INPUT_KINDS)


# ---------------------------------------------------------------------------
# Wiring: the CLI report and the SPA snapshot carry the same checks
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_probes(monkeypatch: Any) -> None:
    """The doctor probes answer offline, the way test_cli_doctor fakes them."""
    from corpus_kb._setup import doctor_research, doctor_storage, install, setup_steps

    async def up_postgres(conn_str: str) -> tuple[bool, str]:
        return True, "PostgreSQL 16.4"

    async def up_ollama(base_url: str) -> tuple[bool, str]:
        return True, "ok"

    async def no_extensions(conn_str: str) -> dict[str, tuple[bool, str]]:
        from corpus_kb._setup.setup_steps import EXTENSIONS

        return {name: (True, "1") for name, _ in EXTENSIONS}

    async def no_storage(config: Any, conn_str: str, postgres_ok: bool) -> list[Any]:
        return []

    async def no_research(
        conn_str: str, config: Any, extensions: Any, ollama_ok: bool | None = None
    ) -> list[Any]:
        return []

    monkeypatch.setattr(install, "check_postgres", up_postgres)
    monkeypatch.setattr(install, "check_ollama", up_ollama)
    monkeypatch.setattr(install, "check_extensions", no_extensions)
    monkeypatch.setattr(doctor_storage, "storage_checks", no_storage)
    monkeypatch.setattr(doctor_research, "research_checks", no_research)
    monkeypatch.setattr(setup_steps, "check_postgres", up_postgres)
    monkeypatch.setattr(setup_steps, "check_ollama", up_ollama)
    monkeypatch.setattr(setup_steps, "check_extensions", no_extensions)


@pytest.mark.asyncio
async def test_cli_doctor_carries_the_journey(
    offline_probes: None, monkeypatch: Any, capsys: Any
) -> None:
    """The CLI --json report and the text report both print the journey."""
    from corpus_kb._setup import install

    seen: dict[str, bool] = {}

    async def fake_journey(
        config: Any, conn_str: str, db_ok: bool, ollama_ok: bool
    ) -> list[ResearchCheck]:
        seen["db_ok"] = db_ok
        seen["ollama_ok"] = ollama_ok
        return [
            ResearchCheck(
                name="journey_next",
                status=STATUS_OK,
                detail="Next: build labels with method 04.",
                fix="corpus-kb labeling-build",
            )
        ]

    monkeypatch.setattr(dj, "journey_checks", fake_journey)
    config: dict[str, Any] = {
        "database": {
            "backend": "postgres",
            "connection_string": "postgresql://corpus_user:pw@localhost:5433/corpus_kb",
        },
        "embedding": {"provider": "ollama"},
    }
    code = await install.doctor_cmd(config, json_output=True)
    report = json.loads(capsys.readouterr().out)
    names = {check["name"] for check in report["checks"]}
    assert "journey_next" in names
    assert code == 0
    assert seen == {"db_ok": True, "ollama_ok": True}

    await install.doctor_cmd(config)
    assert "Your analysis journey:" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_snapshot_carries_the_journey(offline_probes: None, monkeypatch: Any) -> None:
    """The SPA snapshot appends the journey checks to research_checks."""
    from corpus_kb.tools.doctor_tools import doctor_snapshot

    async def fake_journey(
        config: Any, conn_str: str, db_ok: bool, ollama_ok: bool
    ) -> list[ResearchCheck]:
        return [
            ResearchCheck(
                name="journey_state",
                status=STATUS_OK,
                detail="You have 120 chunks and 120 vectors.",
            )
        ]

    monkeypatch.setattr(dj, "journey_checks", fake_journey)
    config: dict[str, Any] = {
        "database": {
            "backend": "postgres",
            "connection_string": "postgresql://corpus_user:pw@localhost:5433/corpus_kb",
        },
        "embedding": {"provider": "ollama"},
    }
    snapshot = await doctor_snapshot(config)
    research = snapshot["result"]["research_checks"]
    assert research[-1]["name"] == "journey_state"
    assert research[-1]["status"] == STATUS_OK

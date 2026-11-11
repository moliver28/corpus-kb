"""Fixture E2E + G0/G5 gates + dynamic-ingest proof (todo-11 (d)/(e)).

Runs on the throwaway research DB (requires_postgres). Embedding goes through
a deterministic 1024-dim FakeEmbedder behind the embedding cache, so no
Ollama is needed; the live-model path is covered separately (requires_ollama).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from research_db import TEST_TENANT

from corpus_kb.errors import ConflictError
from corpus_kb.handlers.research_handler import get_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder

pytestmark = pytest.mark.requires_postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)


class CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__({"embedding": {"dimensions": 1024}})
        self.calls = 0

    def embed_batch(self, texts):  # type: ignore[override]
        self.calls += 1
        return super().embed_batch(texts)


async def _wire(pool):
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(pool, CountingEmbedder(), model_revision="test-1024")
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool)
    return reader, projection, handler, embedder


async def _tenant_counts(pool: asyncpg.Pool, tenant: UUID, table: str) -> list:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(tenant))
        return await conn.fetch(f"SELECT * FROM {table} WHERE tenant_id = $1", str(tenant))


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    source = FIXTURES / "exchange_fixture_interview.md"
    target = tmp_path / "exchange_fixture_interview.md"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


async def test_fixture_e2e_event_stream_and_read_tables(research_pool, fixture_dir):
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    labels = json.loads(
        (FIXTURES / "exchange_fixture_interview.labels.json").read_text(encoding="utf-8")
    )

    project_id = uuid4()
    result = await handler.ingest_transcript(
        TENANT,
        str(fixture_dir / "exchange_fixture_interview.md"),
        project_id=project_id,
    )
    assert result["status"] == "success"
    assert result["n_turns"] == labels["expected_turn_count"]
    assert result["parse_quality"] == "verified"

    await projection.catch_up(reader)

    speakers = await _tenant_counts(research_pool, TENANT, "research_speakers")
    by_label = {r["raw_label"]: r["role"] for r in speakers}
    for speaker, role in labels["role_expectations"].items():
        assert by_label.get(speaker) == role, f"role mismatch for {speaker}"
    assert all(r["role_confirmed"] is False for r in speakers)

    units = await _tenant_counts(research_pool, TENANT, "research_units")
    assert len(units) == labels["expected_turn_count"]
    assert all(r["text"] for r in units), "unit text not resolved from content store"
    assert all(
        r["project_id"] is not None and str(r["project_id"]) == str(project_id) for r in units
    ), "research_units.project_id not resolved from the documents row"
    moderator_units = [
        r for r in units if r["role_in_exchange"] in ("main_question", "probe", "transition")
    ]
    assert all(not r["is_codable"] for r in moderator_units)

    exchanges = await _tenant_counts(research_pool, TENANT, "research_exchanges")
    assert len(exchanges) == labels["expected_exchange_count"]
    for expected_method, row in zip(labels["exchange_link_methods"], exchanges, strict=False):
        assert row["link_method"] == expected_method

    # G5: stance + question-dependent routing (recorded via the event-only path)
    code = next(c for c in labels["codes"])
    handler.create_codebook_version(
        TENANT,
        label="v1-fixture",
        codes=[
            {
                "code_id": code["code_id"],
                "name": code["name"],
                "definition": code["definition"],
                "inclusion": "trust statements",
                "exclusion": "logistics complaints",
            }
        ],
    )
    run = handler.start_coding_run(TENANT, embed_model="fake-1024", model_revision="test")
    run_id = UUID(str(run["run_id"]))
    by_text = {r["text"]: r for r in units}

    def _unit_for(prefix: str):
        match = next((u for t, u in by_text.items() if t.startswith(prefix[:30])), None)
        assert match is not None, f"no unit starts with {prefix[:30]!r}"
        return match

    for unit_text in code["explicit_units"]:
        unit = _unit_for(unit_text)
        handler.record_assignment(
            TENANT,
            unit_id=int(unit["unit_id"]),
            code_id=uuid4(),
            run_id=run_id,
            evidence_basis="explicit_in_answer",
            stance="affirm",
            confidence="high",
        )
    for unit_text in code["question_dependent_units"]:
        unit = _unit_for(unit_text)
        handler.record_assignment(
            TENANT,
            unit_id=int(unit["unit_id"]),
            code_id=uuid4(),
            run_id=run_id,
            evidence_basis="question_dependent",
            stance="partial",
            confidence="high",
            signals=[{"tier": 0, "link_score": 0.9, "hedge_flag": False}],
        )
    await projection.catch_up(reader)

    signals = await _tenant_counts(research_pool, TENANT, "research_signals")
    assert signals, "SignalRecorded produced no research_signals row (M1)"
    assert all(s["unit_id"] != 0 for s in signals), "signal rows carry unit_id=0"

    assignments = await _tenant_counts(research_pool, TENANT, "research_assignments")
    assert len(assignments) == 3
    qdep = [a for a in assignments if a["evidence_basis"] == "question_dependent"]
    assert qdep, "fixture missing question-dependent case"
    assert all(a["status"] == "review" for a in qdep), "question_dependent must route to review"
    assert all(a["confidence"] != "high" for a in qdep), "question_dependent confidence capped"
    explicit = [a for a in assignments if a["evidence_basis"] == "explicit_in_answer"]
    assert all(a["status"] == "auto" for a in explicit)

    # DLQ gains NO new rows from this fixture run (acceptance); rows injected
    # by other tests' induced-failure cases under the shared tenant are not
    # this run's business.
    dlq_rows = await _tenant_counts(research_pool, TENANT, "projection_dlq")
    new_dlq = [r for r in dlq_rows if str(r["dlq_id"]) not in baseline_dlq]
    assert new_dlq == [], f"DLQ gained rows during the fixture run: {new_dlq}"

    # embedding cache filled; units carry 1024-dim vectors
    cache = await _tenant_counts(research_pool, TENANT, "embedding_cache")
    assert cache, "embedding_cache empty after fixture ingest"
    embedded_units = [u for u in units if u["embedding"] is not None]
    assert embedded_units, "no unit vectors written"


async def test_rebuild_zero_embedder_calls_beyond_cache(research_pool, fixture_dir):
    reader, projection, handler, embedder = await _wire(research_pool)
    result = await handler.ingest_transcript(
        TENANT, str(fixture_dir / "exchange_fixture_interview.md")
    )
    doc_id = UUID(str(result["doc_id"]))
    await projection.catch_up(reader)
    calls_after_ingest = embedder._embedder.calls

    from research_db import superuser_conn

    conn = await superuser_conn()
    try:
        await conn.execute(
            "DELETE FROM projection_checkpoints WHERE projection_name='ResearchProjection'"
        )
        await conn.execute(
            "TRUNCATE research_reviews, research_signals, research_assignments, "
            "research_exchanges, research_units, research_speakers, research_projects "
            "RESTART IDENTITY"
        )
    finally:
        await conn.close()

    await projection.catch_up(reader)
    assert embedder._embedder.calls == calls_after_ingest, (
        "rebuild made embedder calls beyond embedding_cache misses"
    )
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        after_units = await conn.fetch(
            "SELECT doc_id, seq, text, text_sha256, embedding "
            "FROM research_units WHERE tenant_id=$1 AND doc_id=$2 ORDER BY seq",
            TENANT,
            str(doc_id),
        )
        after_exchanges = await conn.fetch(
            "SELECT doc_id, seq, link_method, q_unit_ids, a_unit_ids "
            "FROM research_exchanges WHERE tenant_id=$1 AND doc_id=$2 ORDER BY seq",
            TENANT,
            str(doc_id),
        )
    assert after_units, "no units rebuilt for the ingest document"
    assert all(u["text"] for u in after_units), "content store did not survive rebuild drop-set"
    assert any(u["embedding"] is not None for u in after_units), "vectors not re-linked"
    assert after_exchanges, "exchanges not rebuilt"


async def test_dynamic_ingest_two_file_dir_edit_one(research_pool, tmp_path):
    reader, projection, handler, _ = await _wire(research_pool)
    file_a = tmp_path / "a.txt"
    file_a.write_text("Moderator: How is the weather?\nP1: Sunny, mostly.\n", encoding="utf-8")
    file_b = tmp_path / "b.txt"
    file_b.write_text("Moderator: Any final thoughts?\nP1: No, thanks.\n", encoding="utf-8")

    first = await handler.ingest_path(TENANT, str(tmp_path))
    assert first["ingested"] == 2 and first["skipped"] == 0

    second_unchanged = await handler.ingest_path(TENANT, str(tmp_path))
    assert second_unchanged["ingested"] == 0 and second_unchanged["skipped"] == 2

    file_b.write_text(
        "Moderator: Any final thoughts?\nP1: No, thanks. One more thing - pricing.\n",
        encoding="utf-8",
    )
    third = await handler.ingest_path(TENANT, str(tmp_path))
    assert third["ingested"] == 1, "editing ONE file must re-ingest exactly one"
    assert third["skipped"] == 1, "the unchanged file must file-hash no-op"

    await projection.catch_up(reader)
    turns = await _tenant_counts(research_pool, TENANT, "research_transcript_text")
    sunny_rows = [t for t in turns if t["text"] == "Sunny, mostly."]
    assert len(sunny_rows) == 1, "turn-text dedup violated for unchanged turns"


async def test_watch_once_ingests_dropped_file(research_pool, tmp_path):
    """M5 regression: --watch detection must INGEST, not just log."""
    from corpus_kb.research.dynamic_ingest import watch_once

    reader, projection, handler, _ = await _wire(research_pool)
    dropped = tmp_path / "drop"
    dropped.mkdir()

    async def ingest_file(path: Path) -> None:
        await handler.ingest_transcript(TENANT, str(path))
        await projection.catch_up(reader)

    (dropped / "later.txt").write_text("Moderator: Ready to wrap?\nP1: Yes.\n", encoding="utf-8")
    n = await watch_once(research_pool, TENANT, dropped, ingest_file)
    assert n == 1, "watch_once must ingest the dropped file"

    units = await _tenant_counts(research_pool, TENANT, "research_units")
    assert any(u["text"] == "Yes." for u in units), "watched file never projected"

    n2 = await watch_once(research_pool, TENANT, dropped, ingest_file)
    assert n2 == 0, "unchanged file must not re-ingest on the next pass"


async def test_concurrent_assignment_dispatch_no_contention(research_pool):

    reader, projection, handler, _ = await _wire(research_pool)
    run = handler.start_coding_run(TENANT)
    run_id = UUID(str(run["run_id"]))
    import asyncio

    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                handler.record_assignment,
                TENANT,
                unit_id=1000 + i,
                code_id=uuid4(),
                run_id=run_id,
                evidence_basis="explicit_in_answer",
            )
            for i in range(4)
        )
    )
    assert all(r["status"] == "success" for r in results)
    # Drain THIS test's own events through the shared projection before
    # returning: the unit_id 1000+ rows deliberately violate
    # research_assignments_unit_id_fkey and DLQ on projection. If a later
    # test's catch_up advanced the shared checkpoint past them, its
    # before/after DLQ snapshot would wrongly attribute these rows
    # (documented combo flake - DLQ them here, where they belong).
    await projection.catch_up(reader)


# ---------------------------------------------------------------------------
# Re-ingest of the same source replaces in place (issue #363)
# ---------------------------------------------------------------------------

# A transcript the research parser handles: labeled speakers, q/a pairs.
_TRANSCRIPT_V1 = (
    "# Sample interview\n"
    "\n"
    "Moderator: How did onboarding feel?\n"
    "P1 Dana: Rough. The docs were stale.\n"
    "Moderator: What was stale?\n"
    "P1 Dana: The API reference. Endpoints moved.\n"
)
_TRANSCRIPT_V2 = _TRANSCRIPT_V1 + ("Moderator: Anything else?\nP1 Dana: No, that is all.\n")


def _write_transcript(tmp_path: Path, text: str) -> str:
    target = tmp_path / "reingest_sample.md"
    target.write_text(text, encoding="utf-8")
    return str(target)


def _new_dlq_rows(rows, baseline) -> list:
    return [r for r in rows if str(r["dlq_id"]) not in baseline]


def _rows_for_doc(rows, doc_id: UUID) -> list:
    """Scope tenant-wide rows to THIS test's document (the tenant is shared)."""
    return [r for r in rows if UUID(str(r["doc_id"])) == doc_id]


async def test_force_reingest_replaces_in_place(research_pool, tmp_path):
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    transcript = _write_transcript(tmp_path, _TRANSCRIPT_V1)

    first = await handler.ingest_transcript(TENANT, transcript)
    assert first["status"] == "success"
    await projection.catch_up(reader)
    doc_id = UUID(str(first["doc_id"]))

    second = await handler.ingest_transcript(TENANT, transcript, force=True)
    assert second["status"] == "success"
    await projection.catch_up(reader)

    # The aggregate id is stable: one stream, one documents row, one ledger row.
    assert UUID(str(second["doc_id"])) == doc_id, "re-ingest must reuse the document id"
    docs = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "documents"), doc_id)
    assert len(docs) == 1, f"expected one documents row, got {len(docs)}"
    ledger = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "ingested_files"), doc_id)
    assert len(ledger) == 1, "the ledger must point at the real document"
    assert UUID(str(ledger[0]["doc_id"])) == doc_id

    # The re-parse fully replaced the read rows for the doc.
    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert len(units) == int(second["n_turns"]), "unit count must match the new parse"
    exchanges = _rows_for_doc(
        await _tenant_counts(research_pool, TENANT, "research_exchanges"), doc_id
    )
    assert len(exchanges) == int(second["n_exchanges"])

    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"force re-ingest must not DLQ rows: {new_dlq}"


async def test_edited_file_reingest_lands_new_units(research_pool, tmp_path):
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    transcript = _write_transcript(tmp_path, _TRANSCRIPT_V1)

    first = await handler.ingest_transcript(TENANT, transcript)
    assert first["status"] == "success"
    await projection.catch_up(reader)
    doc_id = UUID(str(first["doc_id"]))

    edited = Path(transcript)
    edited.write_text(_TRANSCRIPT_V2, encoding="utf-8")
    second = await handler.ingest_transcript(TENANT, transcript)
    assert second["status"] == "success", "changed content must not be skipped"
    await projection.catch_up(reader)

    assert UUID(str(second["doc_id"])) == doc_id, "re-ingest must reuse the document id"
    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert len(units) == int(second["n_turns"]), "unit count must match the edited parse"
    assert any("No, that is all." in str(u["text"]) for u in units), "new unit text never landed"

    ledger = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "ingested_files"), doc_id)
    assert len(ledger) == 1
    assert UUID(str(ledger[0]["doc_id"])) == doc_id
    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"edited re-ingest must not DLQ rows: {new_dlq}"


# ---------------------------------------------------------------------------
# Close-out review findings on merged #366 (F-1, F-2, F-3, F-4)
# ---------------------------------------------------------------------------

# Prose with no speaker labels at all: the labelled parser drops every
# line, so ZERO turns is an anticipated parse outcome (unverified quality).
_PROSE_ONLY = (
    "# Field notes\n"
    "\n"
    "Plain prose with no speaker labels lives in this text.\n"
    "The parser drops unlabelled lines when no turn exists yet.\n"
)


async def test_reingest_reuses_aggregate_across_path_spellings(
    research_pool, tmp_path, monkeypatch
):
    """F-1: one file under two path spellings keeps ONE aggregate.

    The ledger probe, the ledger write, and the documents upsert must key
    on the same resolved identity. A spelling change that misses the probe
    mints a phantom aggregate, and the #363 corruption returns (DLQ
    orphans, a ledger row on a phantom id, stale units).
    """
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    target = tmp_path / "spelling_sample.md"
    target.write_text(_TRANSCRIPT_V1, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    first = await handler.ingest_transcript(TENANT, "spelling_sample.md")
    assert first["status"] == "success"
    await projection.catch_up(reader)
    doc_id = UUID(str(first["doc_id"]))

    second = await handler.ingest_transcript(TENANT, str(target), force=True)
    assert second["status"] == "success"
    await projection.catch_up(reader)

    assert UUID(str(second["doc_id"])) == doc_id, "the second spelling must reuse, not mint"
    docs = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "documents"), doc_id)
    assert len(docs) == 1, "one documents row carries the resolved source"
    ledger = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "ingested_files"), doc_id)
    assert len(ledger) == 1, "one ledger row names the reused document"
    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert len(units) == int(second["n_turns"]), "the read rows follow the reused id"
    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"a second spelling must not DLQ rows: {new_dlq}"


async def test_zero_turn_reparse_drops_previous_units(research_pool, tmp_path):
    """F-2: a re-parse that yields zero turns still clears the old units."""
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    transcript = _write_transcript(tmp_path, _TRANSCRIPT_V1)
    first = await handler.ingest_transcript(TENANT, transcript)
    assert int(first["n_turns"]) > 0
    await projection.catch_up(reader)
    doc_id = UUID(str(first["doc_id"]))

    Path(transcript).write_text(_PROSE_ONLY, encoding="utf-8")
    second = await handler.ingest_transcript(TENANT, transcript)
    assert second["status"] == "success"
    assert int(second["n_turns"]) == 0, "the prose-only re-parse yields no turns"
    await projection.catch_up(reader)

    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert units == [], "a zero-turn re-parse must drop the previous units"
    exchanges = _rows_for_doc(
        await _tenant_counts(research_pool, TENANT, "research_exchanges"), doc_id
    )
    assert exchanges == [], "a zero-turn re-parse must drop the previous exchanges"
    docs = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "documents"), doc_id)
    assert len(docs) == 1
    assert str(docs[0]["file_hash"]) == second["file_sha256"], "the row must carry the new parse"
    ledger = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "ingested_files"), doc_id)
    assert len(ledger) == 1
    assert UUID(str(ledger[0]["doc_id"])) == doc_id
    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"the zero-turn re-parse must not DLQ rows: {new_dlq}"


async def test_concurrent_reingest_maps_stream_conflict_to_typed_error(
    research_pool, tmp_path, monkeypatch
):
    """F-3: a lost save race surfaces as the taxonomy's ConflictError."""
    import corpus_kb.handlers.research_handler as handler_module
    from corpus_kb.domain.application import get_app

    reader, projection, handler, _ = await _wire(research_pool)
    transcript = _write_transcript(tmp_path, _TRANSCRIPT_V1)
    first = await handler.ingest_transcript(TENANT, transcript)
    doc_id = UUID(str(first["doc_id"]))
    real_app = get_app()

    # The copy this handler is about to reuse, captured before a
    # concurrent writer advances the same stream.
    stale = real_app.repository.get(doc_id)
    winner = real_app.repository.get(doc_id)
    winner.reingest(
        tenant_id=TENANT,
        source=str(winner.source),
        source_type="transcript",
        file_size=1,
        file_hash="9" * 64,
        metadata={},
        replace=True,
    )
    real_app.save(winner)

    class _StaleRepository:
        """Serves the pre-race copy, exactly as a lost race would hold."""

        def __init__(self, aggregate):
            self._aggregate = aggregate

        def get(self, aggregate_id):
            return self._aggregate

    class _StaleApp:
        """Real saves, a stale rehydrate."""

        def __init__(self, real, aggregate):
            self._real = real
            self.repository = _StaleRepository(aggregate)

        def save(self, aggregate):
            return self._real.save(aggregate)

    monkeypatch.setattr(
        handler_module, "get_app", lambda *args, **kwargs: _StaleApp(real_app, stale)
    )
    with pytest.raises(ConflictError):
        await handler.ingest_transcript(TENANT, transcript, force=True)
    await projection.catch_up(reader)


async def test_reingest_to_fewer_turns_drops_stale_tail(research_pool, tmp_path):
    """F-4: units past the new seq range disappear when the parse shrinks."""
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    transcript = _write_transcript(tmp_path, _TRANSCRIPT_V2)
    first = await handler.ingest_transcript(TENANT, transcript)
    assert int(first["n_turns"]) == 6
    await projection.catch_up(reader)
    doc_id = UUID(str(first["doc_id"]))

    Path(transcript).write_text(_TRANSCRIPT_V1, encoding="utf-8")
    second = await handler.ingest_transcript(TENANT, transcript)
    assert int(second["n_turns"]) == 4
    await projection.catch_up(reader)

    assert UUID(str(second["doc_id"])) == doc_id, "the shrink re-ingest reuses the aggregate"
    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert sorted(int(u["seq"]) for u in units) == [0, 1, 2, 3], "the stale tail must be gone"
    assert all("No, that is all." not in str(u["text"]) for u in units)
    exchanges = _rows_for_doc(
        await _tenant_counts(research_pool, TENANT, "research_exchanges"), doc_id
    )
    assert len(exchanges) == int(second["n_exchanges"])
    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"the shrink re-ingest must not DLQ rows: {new_dlq}"


# ---------------------------------------------------------------------------
# Documents-source fallback for a missed ledger probe (#370)
# ---------------------------------------------------------------------------


async def test_pre366_ledger_spelling_falls_back_to_documents_row(research_pool, tmp_path):
    """#370: a ledger row under a non-resolved spelling must not mint.

    Pre-#366 ingests keyed the ledger on the caller's path string, while
    the documents row keyed the resolved source. The resolved-spelling
    ledger probe then missed, the next ingest minted a second aggregate,
    and the documents row kept the old id.
    """
    reader, projection, handler, _ = await _wire(research_pool)
    baseline_dlq = {
        str(r["dlq_id"]) for r in await _tenant_counts(research_pool, TENANT, "projection_dlq")
    }
    target = tmp_path / "legacy_spelling.md"
    target.write_text(_TRANSCRIPT_V1, encoding="utf-8")

    first = await handler.ingest_transcript(TENANT, str(target))
    assert first["status"] == "success"
    doc_id = UUID(str(first["doc_id"]))
    await projection.catch_up(reader)

    # Simulate the pre-#366 row: the ledger keys a NON-resolved spelling
    # and names the aggregate the documents row carries.
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        await conn.execute(
            "DELETE FROM ingested_files WHERE tenant_id = $1 AND path = $2",
            str(TENANT),
            str(target),
        )
        await conn.execute(
            "INSERT INTO ingested_files (tenant_id, path, file_sha256, source_type, doc_id) "
            "VALUES ($1, $2, $3, 'transcript', $4)",
            str(TENANT),
            "legacy_spelling.md",
            first["file_sha256"],
            str(doc_id),
        )

    second = await handler.ingest_transcript(TENANT, str(target))
    assert second["status"] == "success"
    await projection.catch_up(reader)

    assert UUID(str(second["doc_id"])) == doc_id, (
        "the documents row must answer the missed ledger probe"
    )
    docs = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "documents"), doc_id)
    assert len(docs) == 1, "one documents row carries the resolved source"
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        resolved_row = await conn.fetchrow(
            "SELECT doc_id FROM ingested_files WHERE tenant_id = $1 AND path = $2",
            str(TENANT),
            str(target),
        )
    assert resolved_row is not None, "the resolved spelling must gain a ledger row"
    assert UUID(str(resolved_row["doc_id"])) == doc_id
    units = _rows_for_doc(await _tenant_counts(research_pool, TENANT, "research_units"), doc_id)
    assert len(units) == int(second["n_turns"]), "the read rows follow the reused id"
    new_dlq = _new_dlq_rows(
        await _tenant_counts(research_pool, TENANT, "projection_dlq"), baseline_dlq
    )
    assert new_dlq == [], f"the fallback reuse must not DLQ rows: {new_dlq}"


async def test_documents_probe_scopes_to_research_transcript_rows(research_pool):
    """#370 scope: the documents probe only answers research transcript rows.

    documents.source is shared with the corpus ingestion path, which also
    mints Document aggregates. A corpus-shaped row at the same source must
    never answer the research fallback probe.
    """
    from corpus_kb.research.dynamic_ingest import documents_source_doc_id

    corpus_id, research_id = uuid4(), uuid4()
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        await conn.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type) "
            "VALUES ($1, $2, $3, 'text')",
            str(corpus_id),
            str(TENANT),
            "/parity/corpus_source.md",
        )
        await conn.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, parser_name) "
            "VALUES ($1, $2, $3, 'transcript', 'research.transcript_parser')",
            str(research_id),
            str(TENANT),
            "/parity/research_source.md",
        )
    try:
        assert (
            await documents_source_doc_id(research_pool, TENANT, "/parity/corpus_source.md") is None
        ), "a corpus-shaped documents row must not answer the probe"
        found = await documents_source_doc_id(research_pool, TENANT, "/parity/research_source.md")
        assert found == research_id, "a research transcript documents row answers the probe"
    finally:
        async with research_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
            await conn.execute(
                "DELETE FROM documents WHERE tenant_id = $1 AND source IN ($2, $3)",
                str(TENANT),
                "/parity/corpus_source.md",
                "/parity/research_source.md",
            )

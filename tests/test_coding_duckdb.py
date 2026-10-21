"""Coding-domain store legs on DuckDB (storage portability program, §8).

Hermetic: one migrated DuckDB file behind the SqlGateway, a deterministic
one-hot embed double, and an injected coder reply — no Postgres, no Ollama.
The proofs, one per store module:

- codebook load is content-hash idempotent and writes the registry (the
  gateway arm's ``::FLOAT[2560]`` casts are on this path);
- keyword hits, similarity pooling (upsert + the gateway's parked-row
  demote path) and coverage reconciliation hold together;
- the coding run lifecycle writes chunk_codes + final_codes, re-dispatch is
  a duplicate no-op, and a later pass flips the review status;
- reliability, saturation, floors calibration, keyword governance and
  speaker-role classification all read/write through the same gateway.

Postgres keeps its shipped pool path (byte-identical SQL); the wave1
contract tests pin that seam.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.coding._scope import tenant_scope
from corpus_kb.coding.codebook_loader import load_codebook
from corpus_kb.coding.coder_dispatch import dispatch_chunk
from corpus_kb.coding.floors_calibration import calibrate_code_floors
from corpus_kb.coding.keyword_governance_report import compute_keyword_governance
from corpus_kb.coding.pooling import (
    materialize_chunk_keyword_hits,
    reconcile_coverage,
    run_similarity_pooling,
)
from corpus_kb.coding.saturation_query import compute_saturation
from corpus_kb.coding.speaker_role_projection import classify_speaker_roles
from corpus_kb.handlers.coding_handler import CodingHandler
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

DIM = 2560
TENANT = UUID("00000000-0000-0000-0000-000000000001")
CHUNK_TEXT = "We trust in management here."

_CODEBOOK: dict[str, Any] = {
    "paradigm": "codebook",
    "categories": [
        {
            "id": "C1",
            "name": "trust",
            "brief_definition": "trust in management",
            "inclusion_criteria": "confidence statements",
            "exclusion_criteria": "e-trust",
            "examples": [],
            "theory": {},
        },
        {
            "id": "C2",
            "name": "fear",
            "brief_definition": "fear of layoffs",
            "inclusion_criteria": "i-fear",
            "exclusion_criteria": "e-fear",
            "examples": [],
            "theory": {},
        },
    ],
}


def _one_hot(text: str, instructed: bool = False) -> list[float]:
    index = int(hashlib.sha256(f"{instructed}:{text}".encode()).hexdigest(), 16) % DIM
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


async def _embed(text: str, instructed: bool = False) -> str:
    """Compact array literal of a deterministic one-hot vector.

    Identical texts embed identically (cosine 1.0), different texts are
    orthogonal (cosine 0.0), so similarity outcomes are exact.
    """
    return "[" + ",".join(format(float(v), ".9g") for v in _one_hot(text, instructed)) + "]"


async def _gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / "coding.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _seed_document(gw: DuckDbGateway, doc_id: str, chunk: str, text: str) -> UUID:
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, file_hash) VALUES ($1,$2,$3,$4)"
        " ON CONFLICT DO NOTHING",
        doc_id,
        str(TENANT),
        f"file://{doc_id}.txt",
        uuid4().hex,
    )
    await gw.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, metadata)"
        " VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
        chunk,
        str(TENANT),
        doc_id,
        0,
        text,
        "{}",
    )
    return UUID(chunk)


async def _seed_chunk_vector(gw: DuckDbGateway, chunk: UUID, text: str) -> None:
    """The chunk's stored vector, equal to the code probe pooling compares
    against (the INSTRUCTED embedding — cosine 1.0 exactly)."""
    literal = "[" + ",".join(format(float(v), ".9g") for v in _one_hot(text, True)) + "]"
    await gw.execute(
        "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding) VALUES ($1,$2,$3::FLOAT[2560])"
        " ON CONFLICT (chunk_id) DO NOTHING",
        str(chunk),
        str(TENANT),
        literal,
    )


async def _load(gw: DuckDbGateway, embed: Any = _embed) -> UUID:
    return await load_codebook(_CODEBOOK, gw, str(TENANT), embed)


def _coder_reply(decision: str, quote: str | None = None) -> dict[str, Any]:
    return {
        "decisions": [
            {
                "code_id": "C1",
                "decision": decision,
                "confidence": 0.9 if decision == "assign" else 0.95,
                "evidence_quote": quote if decision == "assign" else None,
                "rationale": "r",
            }
        ],
        "logprobs": [],
    }


def _coder_fn(decision: str, quote: str | None = None) -> Any:
    """An async call_fn double returning one valid coder reply."""

    async def call(window: str) -> dict[str, Any]:
        return _coder_reply(decision, quote)

    return call


async def test_codebook_load_is_content_hash_idempotent(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    calls = 0

    async def counting_embed(text: str, instructed: bool = False) -> str:
        nonlocal calls
        calls += 1
        return await _embed(text, instructed)

    version = await _load(gw, counting_embed)
    assert calls == 4  # two codes x (definition + instructed probe)
    rows = await gw.fetch(
        "SELECT code_id FROM code_registry WHERE tenant_id=$1 AND codebook_version_id=$2"
        " ORDER BY code_id",
        str(TENANT),
        str(version),
    )
    assert [r["code_id"] for r in rows] == ["C1", "C2"]

    again = await load_codebook(_CODEBOOK, gw, str(TENANT), counting_embed)
    assert again == version
    assert calls == 4  # a re-submit re-embeds nothing


async def test_keyword_hits_and_coverage_backfill(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    version = await _load(gw)
    await gw.execute(
        "INSERT INTO code_keywords (keyword, code_id, tenant_id, kind, source)"
        " VALUES ('trust', 'C1', $1, 'inclusion', 'codebook')",
        str(TENANT),
    )
    chunk = await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)

    summary = await materialize_chunk_keyword_hits(gw, TENANT, version)
    assert summary == {"keyword_hits_written": 1, "status": "success"}
    hit = await gw.fetchrow(
        "SELECT n_hits, kind FROM chunk_keyword_hits WHERE tenant_id=$1 AND chunk_id=$2",
        str(TENANT),
        str(chunk),
    )
    assert hit is not None and hit["n_hits"] == 1 and hit["kind"] == "inclusion"

    coverage = await reconcile_coverage(gw, TENANT)
    assert coverage == {
        "total_corpus_chunks": 1,
        "pooled_chunks": 0,
        "never_pooled_chunks": 1,
        "coverage_reconciled": True,
    }
    status = await gw.fetchrow(
        "SELECT doc_id, in_any_pool FROM chunk_status WHERE chunk_id=$1", str(chunk)
    )
    assert status is not None and status["doc_id"] == "d1" and status["in_any_pool"] is False


async def test_similarity_pool_upsert_then_demote_parks_row(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    version = await _load(gw)
    # The chunk vector is identical to C1's instructed probe (cosine 1.0)
    # and orthogonal to C2's (cosine 0.0).
    chunk = await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)
    await _seed_chunk_vector(
        gw,
        chunk,
        "trust in management",
    )

    summary = await run_similarity_pooling(gw, TENANT, version)
    assert summary == {"chunk_signals_written": 1, "status": "success"}
    row = await gw.fetchrow(
        "SELECT score, detail FROM chunk_signals WHERE chunk_id=$1 AND code_id='C1'",
        str(chunk),
    )
    assert row is not None and row["score"] == 1.0 and row["detail"] == "sim=1.0000"

    coverage = await reconcile_coverage(gw, TENANT)
    assert coverage["pooled_chunks"] == 1

    # Raise the floor past 1.0: the pair no longer qualifies. The gateway
    # cannot DELETE chunk_signals, so the demote path parks the row at
    # score NULL and coverage drops the chunk out of the pool.
    await gw.execute(
        "UPDATE code_registry SET pool_floor=1.1 WHERE tenant_id=$1 AND code_id='C1'",
        str(TENANT),
    )
    demoted = await run_similarity_pooling(gw, TENANT, version)
    assert demoted == {"chunk_signals_written": 0, "status": "success"}
    parked = await gw.fetchrow(
        "SELECT score, detail FROM chunk_signals WHERE chunk_id=$1 AND code_id='C1'",
        str(chunk),
    )
    assert parked is not None and parked["score"] is None
    assert parked["detail"] == "below_pool_floor"
    after = await reconcile_coverage(gw, TENANT)
    assert after["pooled_chunks"] == 0 and after["never_pooled_chunks"] == 1


async def test_dispatch_run_lifecycle_duplicate_and_status_flip(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    await _load(gw)
    chunk = await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)

    first = await dispatch_chunk(
        gw,
        TENANT,
        chunk,
        ["C1"],
        call_fn=_coder_fn("assign", "trust in management"),
        batch_id="run-1",
    )
    assert first["status"] == "success" and first["written"] == 1
    audit = await gw.fetchrow(
        "SELECT route, decision, batch_id FROM chunk_codes WHERE tenant_id=$1 AND chunk_id=$2",
        str(TENANT),
        str(chunk),
    )
    assert audit is not None
    assert audit["route"] == "high-conf-accept" and audit["batch_id"] == "run-1"
    final = await gw.fetchrow(
        "SELECT status FROM final_codes WHERE tenant_id=$1 AND chunk_id=$2",
        str(TENANT),
        str(chunk),
    )
    assert final is not None and final["status"] == "accepted"

    dup = await dispatch_chunk(
        gw, TENANT, chunk, ["C1"], call_fn=_coder_fn("assign", "trust in management")
    )
    assert dup["status"] == "success" and dup["duplicates"] == 1 and dup["written"] == 0
    count = await gw.fetchval(
        "SELECT COUNT(*) FROM chunk_codes WHERE tenant_id=$1 AND chunk_id=$2",
        str(TENANT),
        str(chunk),
    )
    assert count == 1  # append-only: the re-dispatch wrote nothing

    flip = await dispatch_chunk(
        gw, TENANT, chunk, ["C1"], call_fn=_coder_fn("reject"), coding_pass=2
    )
    assert flip["status"] == "success" and flip["written"] == 1
    flipped = await gw.fetchrow(
        "SELECT status FROM final_codes WHERE tenant_id=$1 AND chunk_id=$2",
        str(TENANT),
        str(chunk),
    )
    assert flipped is not None and flipped["status"] == "rejected"


async def test_reliability_and_saturation_through_handler(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    await _load(gw)
    chunk_a = await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)
    chunk_b = await _seed_document(gw, "d2", str(uuid4()), "Management has our trust.")
    assign = _coder_fn("assign", "trust in management")
    for chunk in (chunk_a, chunk_b):
        for coder in ("coder-a", "coder-b"):
            await dispatch_chunk(
                gw, TENANT, chunk, ["C1"], coder=coder, call_fn=assign, batch_id="run-1"
            )

    handler = CodingHandler(gw)
    report = await handler.handle_reliability(TENANT)
    assert report["status"] == "success"
    assert report["coders"] == ["coder-a", "coder-b"]
    assert report["n_sheets"] == 2 and report["insufficient_sample"] is False
    assert report["sampled_chunks"] >= 1

    saturation = await compute_saturation(gw, TENANT, batch_id="run-1")
    assert saturation["status"] == "success"
    assert saturation["unique_codes"] == 1
    assert saturation["total_applications"] == 4
    assert saturation["should_stop"] is not None


async def test_floors_calibration_writes_registry_floors(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    version = await _load(gw)
    chunk = await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)
    await _seed_chunk_vector(gw, chunk, "trust in management")

    result = await calibrate_code_floors(gw, TENANT, "C1", version, [chunk], [], min_pos=1)
    assert result["status"] == "success"
    assert result["n_positive_used"] == 1 and result["fallback_applied"] is False
    floor = await gw.fetchval(
        "SELECT pool_floor FROM code_registry WHERE tenant_id=$1 AND code_id='C1'"
        " AND codebook_version_id=$2",
        str(TENANT),
        str(version),
    )
    assert floor is not None and floor == result["pool_floor"]

    with pytest.raises(ValueError, match="probe_vector"):
        await calibrate_code_floors(gw, TENANT, "nope", version, [chunk], [])


async def test_keyword_governance_flags_collision_and_writes_keyness(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    version = await _load(gw)
    await gw.execute(
        "INSERT INTO code_keywords (keyword, code_id, tenant_id, kind, source) VALUES"
        " ('trust', 'C1', $1, 'inclusion', 'codebook'),"
        " ('trust', 'C2', $1, 'inclusion', 'codebook')",
        str(TENANT),
    )
    await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)
    await materialize_chunk_keyword_hits(gw, TENANT, version)

    report = await compute_keyword_governance(gw, TENANT, version)
    assert report["collisions"] == ["trust"]
    assert report["resolutions"]["trust"] is None  # no inclusion_criteria names it
    keyness = await gw.fetch(
        "SELECT ll FROM code_keywords WHERE tenant_id=$1 AND keyword='trust' ORDER BY code_id",
        str(TENANT),
    )
    assert all(r["ll"] is not None for r in keyness)


async def test_speaker_roles_classify_after_backfill(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    await _load(gw)
    await _seed_document(gw, "d1", str(uuid4()), CHUNK_TEXT)
    await reconcile_coverage(gw, TENANT)  # backfills chunk_status

    outcome = await classify_speaker_roles(gw, TENANT)
    assert outcome["chunks_classified"] == 0  # no speaker metadata seeded
    assert outcome["role_counts"] == {"moderator": 0, "participant": 0, "unknown": 0}


async def test_tenant_scope_gateway_enters_transaction(tmp_path: Path) -> None:
    from tests.mock_pg import FakeSqlGateway

    fake = FakeSqlGateway()
    async with tenant_scope(fake, TENANT) as scoped:
        assert scoped is fake
    assert fake.commits == 1 and fake.rollbacks == 0
    assert fake.tenant_ids == [str(TENANT)]

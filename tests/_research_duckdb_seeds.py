"""Seed fixtures for the DuckDB CLI research flow tests (not tests themselves).

The two ``wired_store(embedder=True)`` CLI flows need read models on a
real migrated DuckDB file: ask ranks exchanges (units + embeddings), the
uncoded radar needs a codebook with one coded gold unit. Kept here so the
test module stays under the 250-line limit. Import as
``tests._research_duckdb_seeds`` (the live_services pattern).
"""

from __future__ import annotations

import hashlib
import json
import math
from uuid import uuid4

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.rag.embedder import FakeEmbedder, instruct
from corpus_kb.storage.duckdb_support import vector_literal

TENANT = DEFAULT_TENANT_ID
PROJECT = "11111111-1111-1111-1111-111111111111"
VERSION = "22222222-2222-2222-2222-222222222222"
DIM = 1024
QUESTION = "grid investment"


def normalized(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def probe(text: str) -> list[float]:
    fake = FakeEmbedder({"embedding": {"dimensions": DIM}})
    return normalized(fake.embed(instruct(text)))


def unit_vector(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


def slice_256(vec: list[float]) -> list[float]:
    sliced = vec[:256]
    norm = math.sqrt(sum(x * x for x in sliced)) or 1.0
    return [x / norm for x in sliced]


async def seed_ask_fixture(dsn: str) -> None:
    """Two same-project exchanges whose texts answer ``QUESTION`` lexically."""
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(dsn)
    try:
        doc_a, doc_b = str(uuid4()), str(uuid4())
        for doc_id, source in ((doc_a, "sym://a"), (doc_b, "sym://b")):
            await gateway.execute(
                "INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id,"
                " title) VALUES ($1, $2, $3, 'transcript', $4, $5)",
                doc_id,
                TENANT,
                source,
                PROJECT,
                source,
            )
        await gateway.execute(
            "INSERT INTO research_projects (project_id, tenant_id, name) VALUES ($1, $2, $3)",
            PROJECT,
            TENANT,
            "climate",
        )

        async def _unit(doc_id: str, seq: int, text: str, vec: list[float]) -> int:
            await gateway.execute(
                "INSERT INTO research_speakers (tenant_id, doc_id, raw_label, role)"
                " VALUES ($1, $2, $3, 'participant')",
                TENANT,
                doc_id,
                f"P{seq}",
            )
            await gateway.execute(
                "INSERT INTO research_units (tenant_id, doc_id, speaker_id, seq, text,"
                " text_sha256, role_in_exchange, embedding)"
                " VALUES ($1, $2, (SELECT speaker_id FROM research_speakers"
                "  WHERE tenant_id = $1 AND doc_id = $2 AND raw_label = $3), $4, $5, $6,"
                " 'answer', $7::FLOAT[1024])",
                TENANT,
                doc_id,
                f"P{seq}",
                seq,
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                vector_literal(vec),
            )
            row = await gateway.fetchval(
                "SELECT unit_id FROM research_units"
                " WHERE tenant_id = $1 AND doc_id = $2 AND seq = $3",
                TENANT,
                doc_id,
                seq,
            )
            return int(row)

        on_probe = probe(QUESTION)
        off_probe = normalized(probe("unrelated topic matter")[::-1])
        a1 = await _unit(doc_a, 1, "The grid investment plan accelerates.", on_probe)
        a2 = await _unit(doc_b, 2, "The grid investment plan stalls.", off_probe)
        for doc_id, seq, a_id, vec in ((doc_a, 1, a1, on_probe), (doc_b, 2, a2, off_probe)):
            await gateway.execute(
                "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids,"
                " a_unit_ids, question_text, qa_text, embedding)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::FLOAT[1024])",
                TENANT,
                doc_id,
                seq,
                [],
                [a_id],
                "What about the grid investment plan?",
                "Q: What about the grid investment plan? A: see the answer",
                vector_literal(vec),
            )
    finally:
        await gateway.close()


async def seed_radar_fixture(dsn: str) -> None:
    """One coded gold unit; units 2-4 stay uncoded (the missing-code radar)."""
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(dsn)
    try:
        await gateway.execute(
            "INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm)"
            " VALUES (?, ?, ?, ?, ?)",
            VERSION,
            TENANT,
            "v1",
            "b" * 64,
            "deductive",
        )
        await gateway.execute(
            "INSERT INTO code_registry (code_id, tenant_id, codebook_version_id, name,"
            " brief_definition, inclusion_criteria, exclusion_criteria, theory)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON)",
            "c-trust",
            TENANT,
            VERSION,
            "trust",
            "Expressions of trust.",
            "includes",
            "excludes",
            json.dumps({"exemplar_text_sha256": [format(1, "064x")]}),
        )
        await gateway.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ('d1', ?, 'radar://interview', 'transcript')",
            TENANT,
        )
        for seq in range(1, 5):
            vec = unit_vector(seq)
            await gateway.execute(
                "INSERT INTO research_units (tenant_id, doc_id, seq, text, text_sha256,"
                " role_in_exchange, is_codable, embedding, embedding_256)"
                " VALUES (?, 'd1', ?, ?, ?, 'answer', TRUE, ?::FLOAT[1024], ?::FLOAT[256])",
                TENANT,
                seq,
                f"answer {seq}",
                format(seq, "064x"),
                vector_literal(vec),
                vector_literal(slice_256(vec)),
            )
        await gateway.execute(
            "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
            " question_text, qa_text, embedding)"
            " VALUES (?, 'd1', 1, [1], [1], 'q', 'qa', ?::FLOAT[1024])",
            TENANT,
            vector_literal(unit_vector(1)),
        )
        await gateway.execute(
            "UPDATE research_units SET exchange_id = 1 WHERE tenant_id = ?", TENANT
        )
        await gateway.execute(
            "INSERT INTO research_assignments (tenant_id, assignment_aggregate_id, unit_id,"
            " code_id, cb_version_id, evidence_basis, confidence, sim_answer, rationale)"
            " VALUES (?, 'asg-1', 1, 'c-trust', ?, 'explicit_in_answer', 'high', 0.9, 'seeded')",
            TENANT,
            VERSION,
        )
    finally:
        await gateway.close()


async def tilt_unit_three(dsn: str) -> None:
    """Give unit 3 a non-orthogonal embedding: 0.5*e1 + e3, unit length.

    The radar test's order then proves discrimination, not a sort tie:
    unit 3's residual to the coded prototype rises to ~0.45 over the
    other units' 0.0, so the semantic order is [2, 4, 3].
    """
    from corpus_kb.storage.gateway import DuckDbGateway

    tilted = [0.5 * (i == 0) + (1.0 * (i == 2)) for i in range(DIM)]
    tilted = normalized(tilted)
    gateway = await DuckDbGateway.connect(dsn)
    try:
        await gateway.execute(
            "UPDATE research_units SET embedding = ?::FLOAT[1024],"
            " embedding_256 = ?::FLOAT[256] WHERE tenant_id = ? AND seq = 3",
            vector_literal(tilted),
            vector_literal(slice_256(tilted)),
            TENANT,
        )
    finally:
        await gateway.close()

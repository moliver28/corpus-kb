"""Hermetic research-views + governance-report legs on DuckDB (sp-release-port).

The three notebook views run over the :class:`DuckDbGateway` through the
tenant-connection seam (no Postgres, no Ollama): evidence, and — since the
engine dispatch landed (``duck=`` on the kNN runners plus the DuckDB
centroid twin) — uncoded and overlap too. The governance report sections
are pure functions — the leg pins their SNAPSHOT STABILITY (program rule
9): two input orders must produce identical sections. The CLI module
itself is storage-blind.
"""

from __future__ import annotations

import json
import math
from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal

TENANT = UUID("00000000-0000-0000-0000-000000000001")
VERSION = "11111111-1111-1111-1111-111111111111"
DIM = 1024


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


def _slice_256(vec: list[float]) -> list[float]:
    sliced = vec[:256]
    norm = math.sqrt(sum(x * x for x in sliced)) or 1.0
    return [x / norm for x in sliced]


@pytest.fixture
async def gateway(tmp_path):
    db = tmp_path / "views.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    yield gw
    await gw.close()


async def _seed_evidence_fixture(gw: DuckDbGateway) -> None:
    version = "11111111-1111-1111-1111-111111111111"
    await gw.execute(
        "INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm) "
        "VALUES (?, ?, ?, ?, ?)",
        version,
        str(TENANT),
        "v1",
        "b" * 64,
        "deductive",
    )
    await gw.execute(
        "INSERT INTO code_registry (code_id, tenant_id, codebook_version_id, name, "
        "brief_definition, inclusion_criteria, exclusion_criteria) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        "c-trust",
        str(TENANT),
        version,
        "trust",
        "Expressions of trust.",
        "includes",
        "excludes",
    )
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id, title) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        "d1",
        str(TENANT),
        "interview.md",
        "markdown",
        "p1",
        "Interview one",
    )
    units = [
        (1, "they delivered on time", "high", 0.90, "explicit_in_answer"),
        (2, "the schedule held", "medium", 0.95, "explicit_in_answer"),
        (3, "i expect the same next phase", "high", 0.80, "question_dependent"),
    ]
    for unit_id, text, confidence, sim, basis in units:
        await gw.execute(
            "INSERT INTO research_units (tenant_id, doc_id, seq, text, text_sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            str(TENANT),
            "d1",
            unit_id,
            text,
            format(unit_id, "064x"),
        )
        await gw.execute(
            "INSERT INTO research_assignments (tenant_id, assignment_aggregate_id, unit_id, "
            "code_id, evidence_basis, confidence, sim_answer, rationale) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            str(TENANT),
            f"asg-{unit_id}",
            unit_id,
            "c-trust",
            basis,
            confidence,
            sim,
            "seeded",
        )
    await gw.execute(
        "INSERT INTO research_signals (tenant_id, unit_id, tier, semantic_entropy) "
        "VALUES (?, ?, ?, ?)",
        str(TENANT),
        2,
        3,
        1.5,
    )


async def test_evidence_view_orders_stably_on_duckdb(gateway: DuckDbGateway) -> None:
    from corpus_kb.research.notebook_views import evidence_for_code

    await _seed_evidence_fixture(gateway)
    result = await evidence_for_code(gateway, TENANT, "trust")
    assert result["status"] == "ok"
    assert result["code"] == "trust"
    assert result["n_units"] == 3

    explicit = result["tabs"]["explicit"]
    # Confidence tier first, then -sim_answer, then unit_id (program rule 9).
    assert [(e["unit_id"], e["confidence"]) for e in explicit] == [
        (1, "high"),
        (2, "medium"),
    ]
    question_dependent = result["tabs"]["question_dependent"]
    assert [e["unit_id"] for e in question_dependent] == [3]

    by_unit = {e["unit_id"]: e for tab in result["tabs"].values() for e in tab}
    assert by_unit[2]["disagreement"] is True
    assert by_unit[1]["disagreement"] is False
    assert all(e["conformal_set_size"] == 1 for e in by_unit.values())
    assert by_unit[1]["doc_title"] == "Interview one"

    # Snapshot stability: the same rows, the same output.
    again = await evidence_for_code(gateway, TENANT, "trust")
    assert again == result


async def test_evidence_view_reports_unknown_code_on_duckdb(gateway: DuckDbGateway) -> None:
    from corpus_kb.research.notebook_views import evidence_for_code

    await _seed_evidence_fixture(gateway)
    result = await evidence_for_code(gateway, TENANT, "no-such-code")
    assert result["status"] == "empty"
    assert "no-such-code" in result["message"]


async def _seed_radar_fixture(gw: DuckDbGateway) -> None:
    """One code with one gold exemplar; unit 1 coded, units 2-4 uncoded.

    Every unit carries the 1024-d vector and its 256-d MRL slice, so the
    radar's 256-d retrieval leg and the full-dim re-rank both have data.
    """
    await gw.execute(
        "INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm)"
        " VALUES (?, ?, ?, ?, ?)",
        VERSION,
        str(TENANT),
        "v1",
        "b" * 64,
        "deductive",
    )
    await gw.execute(
        "INSERT INTO code_registry (code_id, tenant_id, codebook_version_id, name,"
        " brief_definition, inclusion_criteria, exclusion_criteria, theory)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?::JSON)",
        "c-trust",
        str(TENANT),
        VERSION,
        "trust",
        "Expressions of trust.",
        "includes",
        "excludes",
        json.dumps({"exemplar_text_sha256": [format(1, "064x")]}),
    )
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ('d1', ?, 'radar://interview', 'transcript')",
        str(TENANT),
    )
    for seq in range(1, 5):
        vec = _unit_vector(seq)
        await gw.execute(
            "INSERT INTO research_units (tenant_id, doc_id, seq, text, text_sha256,"
            " role_in_exchange, is_codable, embedding, embedding_256)"
            " VALUES (?, 'd1', ?, ?, ?, 'answer', TRUE, ?::FLOAT[1024], ?::FLOAT[256])",
            str(TENANT),
            seq,
            f"answer {seq}",
            format(seq, "064x"),
            vector_literal(vec),
            vector_literal(_slice_256(vec)),
        )
    await gw.execute(
        "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
        " question_text, qa_text, embedding)"
        " VALUES (?, 'd1', 1, [1], [1], 'q', 'qa', ?::FLOAT[1024])",
        str(TENANT),
        vector_literal(_unit_vector(1)),
    )
    await gw.execute("UPDATE research_units SET exchange_id = 1 WHERE tenant_id = ?", str(TENANT))
    await gw.execute(
        "INSERT INTO research_assignments (tenant_id, assignment_aggregate_id, unit_id,"
        " code_id, cb_version_id, evidence_basis, confidence, sim_answer, rationale)"
        " VALUES (?, 'asg-1', 1, 'c-trust', ?, 'explicit_in_answer', 'high', 0.9, 'seeded')",
        str(TENANT),
        VERSION,
    )


async def test_uncoded_view_ranks_the_radar_on_duckdb(gateway: DuckDbGateway) -> None:
    from corpus_kb.research.notebook_views import uncoded_units

    await _seed_radar_fixture(gateway)
    result = await uncoded_units(gateway, None, TENANT)
    assert result["status"] == "ok"
    assert result["version_id"] == VERSION
    assert result["n_uncoded"] == 3
    # Ascending residual: the orthogonal uncoded units tie at 0.0 and the
    # stable-id tiebreaker orders them; the coded gold unit never surfaces.
    assert [u["unit_id"] for u in result["units"]] == [2, 3, 4]
    assert all(u["margin"] == 1.0 for u in result["units"])

    # Snapshot stability: the same rows, the same output (program rule 9).
    again = await uncoded_units(gateway, None, TENANT)
    assert again == result


async def test_overlap_view_builds_the_section_on_duckdb(gateway: DuckDbGateway) -> None:
    from corpus_kb.research.notebook_views import overlap_view

    await _seed_radar_fixture(gateway)
    result = await overlap_view(gateway, TENANT)
    assert result["status"] == "ok"
    assert result["version_id"] == VERSION
    # One coded code: the centroid matrix is 1x1 with no pairs to flag, and
    # unit 1 has a single assignment, so no top-2 margin is borderline.
    assert result["matrix_codes"] == ["c-trust"]
    assert result["matrix"] == [[1.0]]
    assert result["flagged_pairs"] == []
    assert result["borderline_units"] == []
    assert "c-trust" in result["per_code_silhouette"]

    again = await overlap_view(gateway, TENANT)
    assert again == result


def test_governance_sections_stable_under_input_order() -> None:
    """Two input orders, identical sections (program rule 9)."""
    from corpus_kb.research.report_governance import keywords_section, overlap_section

    definitions = {
        "trust": {
            "theory": {
                "keywords": [
                    {"term": "reliable", "score": 0.9, "polarity": "positive", "support_n": 30},
                    {"term": "danger", "score": 0.4, "polarity": "positive", "support_n": 21},
                    {"term": "risk", "score": 0.5, "polarity": "exclusion", "support_n": 20},
                ]
            }
        },
        "risk": {
            "theory": {
                "keywords": [
                    {"term": "reliable", "score": 0.8, "polarity": "positive", "support_n": 25},
                    {"term": "danger", "score": 0.7, "polarity": "exclusion", "support_n": 18},
                ]
            }
        },
    }
    reversed_definitions = dict(reversed(list(definitions.items())))
    section_a = keywords_section(definitions, {})
    section_b = keywords_section(reversed_definitions, {})
    assert section_a == section_b
    flag_types = {flag["conflict_type"] for flag in section_a["conflicts"]}
    assert "shared_positive" in flag_types
    assert "positive_vs_exclusion" in flag_types

    centroids_a = {"trust": [1.0, 0.0], "risk": [0.95, 0.05]}
    centroids_b = {"risk": [0.95, 0.05], "trust": [1.0, 0.0]}
    coded_vectors = [
        {"code_id": "trust", "vec": [1.0, 0.0]},
        {"code_id": "risk", "vec": [0.95, 0.05]},
    ]
    assignments = [
        {"code_id": "trust", "unit_id": 1, "sim_answer": 0.9},
        {"code_id": "trust", "unit_id": 2, "sim_answer": 0.88},
        {"code_id": "risk", "unit_id": 2, "sim_answer": 0.87},
    ]
    overlap_a = overlap_section(centroids_a, coded_vectors, assignments, 0.9)
    overlap_b = overlap_section(centroids_b, coded_vectors, assignments, 0.9)
    assert overlap_a == overlap_b
    assert overlap_a["matrix_codes"] == ["risk", "trust"]
    assert overlap_a["flagged_pairs"], "near-identical centroids flag above tau_overlap"

"""Hermetic research-views + governance-report legs on DuckDB (sp-release-port).

The evidence view runs over the :class:`DuckDbGateway` through the
tenant-connection seam (no Postgres, no Ollama). The governance report
sections are pure functions — the leg pins their SNAPSHOT STABILITY
(program rule 9): two input orders must produce identical sections.
The uncoded/overlap views stay on the Postgres path until the
research-store work package lands (analytics_sql owns the vector legs);
the CLI module itself is storage-blind.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")


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

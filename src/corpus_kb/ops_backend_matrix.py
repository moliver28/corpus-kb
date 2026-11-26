"""Operation x backend declaration matrix (parity program PR-9, rule 12).

The op-level half of the config-backend matrix: ONE row per surface
operation, seeded with the PR-6/PR-7/PR-8 outcomes. The universe is
``tests/test_backend_operation_parity.py::PARITY_OPS`` plus the HTTP-only
``ingest_upload`` (the same universe ``scripts/duck_live_matrix.py::OPS``
pins) — ``tests/test_config_backend_parity_gate.py`` asserts exact set
equality in both directions, so an op cannot appear or die without a row
edit here.

Behaviors carry the designed deltas (G27): the DuckDB query arm runs
exact-scan + BM25 FTS index modes with a disjunctive-only lexical arm and
zero-vector sentinel ordering, and row-level security is Postgres-only by
design. The rows feed the config-backend parity gate and the live matrix
(``scripts/duck_live_matrix.py`` derives its op list from
:func:`declared_ops`); the published matrix doc
(``docs/research/duckdb-parity-matrix.md``) is hand-checked against them.

Split note: the planner pre-authorized ``config_backend_matrix.py``
(config keys) + ``ops_backend_matrix.py`` (ops) so each module stays
under the 250-line ceiling (PR-9, same PR). Data + stdlib only.
"""

from __future__ import annotations

from typing import NamedTuple

from corpus_kb.config_backend_matrix import BOTH, DUCKDB, POSTGRES


class OpsBackendRow(NamedTuple):
    """One operation's backend declaration."""

    op: str
    backends: tuple[str, ...]
    behavior: str
    note: str


def _ops(ops: str, behavior: str, note: str) -> list[OpsBackendRow]:
    """Expand one whitespace-separated op group into per-op rows."""
    return [OpsBackendRow(op, BOTH, behavior, note) for op in ops.split()]


OPS_DELTA_ROWS: tuple[OpsBackendRow, ...] = tuple(
    _ops(
        "topics_list labeling_list labeling_lfs classification_list "
        "analysis_select_method statements_list statements_timeline "
        "conflicts_list beliefs_current queue_enqueue queue_status "
        "ingest_status list_documents get_document_tags get_metadata "
        "release_assess notebook_evidence notebook_uncoded notebook_overlap "
        "delete_document project_provision project_list project_drop "
        "coding_status coding_export events_summary keyphrases_extract "
        "lexical_classify neighbors_classify centroids_classify "
        "clusters_inspect duplicates_screen outliers_rank sentiment_screen "
        "active_learn_next nli_classify contradictions_screen "
        "survey_openend_codes usability_severity emotion_tag labeling_build "
        "classification_build topics_build topics_trends analysis_backfill_dates "
        "analysis_embed_meta analyze_run conflicts_scan labeling_triage "
        "labeling_gold_check queue_cancel research_cycle research_demo "
        "demo_load extraction_tune ingest_url release_compare ingest_reingest "
        "ingest_directory",
        "honored-both",
        "Proved on DuckDB by the PR-8 parity legs (PARITY_OPS); shared handlers.",
    )
    + _ops(
        "query search_context verify_answer notebook_ask",
        "honored-both-duck-arm-delta-by-design",
        "G27: DuckDB runs exact_scan + fts_bm25 index modes, a disjunctive-only "
        "lexical arm, and zero-vector sentinel ordering.",
    )
    + _ops(
        "add_entity add_relation search_graph get_entity_relations graph_neighbors graph_traverse",
        "honored-both-rls-pg-only-by-design",
        "G27: row-level security is Postgres-only; DuckDB traverses via "
        "graph.duckdb_graph_backend (cte or duckpgq).",
    )
    + [
        OpsBackendRow(
            "ingest_upload",
            BOTH,
            "honored-both-http-only",
            "HTTP-only by catalogue ruling; proved live by the PR-8 upload leg.",
        )
    ]
)


def declared_ops() -> tuple[str, ...]:
    """Every declared op name."""
    return tuple(row.op for row in OPS_DELTA_ROWS)


__all__ = [
    "BOTH",
    "DUCKDB",
    "OPS_DELTA_ROWS",
    "POSTGRES",
    "OpsBackendRow",
    "declared_ops",
]

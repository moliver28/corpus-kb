"""Master allowlist of MCP tool names valid in ``autoApprove`` lists.

Two families:
  * the master corpus-kb MCP tool set (search / sql / graph / metadata),
  * the research/coding surfaces (tools/research_tools.py +
    tools/notebook_tools.py — todo-18 tool-name lockstep, r9: every PR
    registering an MCP tool extends this set AND regenerates the harness
    pack in the SAME PR).

Also owns the shared autoApprove list rule used by the OpenCode and
Claude Code validators.
"""

from __future__ import annotations

VALID_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # -- master corpus-kb MCP tools -------------------------------------
        "search",
        "ask",
        "search_context",
        "search_similar",
        "retrieve_context",
        "list_documents",
        "get_stats",
        "list_versions",
        "list_branches",
        "get_entity_relations",
        "search_graph",
        "graph_neighbors",
        "graph_traverse",
        "sql_query",
        "sql_tables",
        "get_document_tags",
        "get_metadata",
        "query_document_stats",
        "sync_database",
        # -- research/coding surfaces (todos 11-18) --------------------------
        "research_ingest_transcript",
        "research_ingest",
        "coding_run",
        "inductive_run",
        "codebook_promote",
        "research_report",
        "review_execute",
        "notebook_ask",
        "notebook_evidence",
        "notebook_uncoded",
        "notebook_overlap",
        "research_cycle",
        "research_demo",
        # -- release lifecycle (U5, tools/release_tools.py, P7) --------------
        "release_assess",
        "release_request",
        "release_review",
        "release_execute",
        "release_status",
        "release_compare",
        # -- ingest front door (Stage 5, tools/ingest_surface_tools.py) ------
        "ingest_status",
        "ingest_reembed",
        # -- pipeline ingest ops (UI SPA Task 5a): catalogue-registered MCP
        #    tools that gained surface_registry rows (manifest longRunning) --
        "ingest_directory",
        "ingest_url",
        # -- ingest queue (textbook program): catalogue-registered MCP tools
        #    with surface_registry rows ------------------------------------
        "queue_enqueue",
        "queue_status",
        "queue_cancel",
        # -- lifecycle UI ops (UI SPA Task 5b, issue #176): registry rows over
        #    the demo loader and the doctor probes ---------------------------
        "demo_load",
        "doctor",
        # -- lifecycle read lists (UI SPA Task 5b, spec B2): the Review and
        #    Codebook screens' GET reads -------------------------------------
        "projects_list",
        "codebooks_list",
        "codebook_proposals",
        "coding_assignments",
        # -- topics layer (Wave 4, tools/topics_tools.py) ---------------------
        "topics_build",
        "topics_list",
        # -- analysis program (Wave 0 skeleton, tools/*_tools.py) -------------
        "topics_trends",
        "labeling_build",
        "labeling_list",
        "labeling_lfs",
        "labeling_triage",
        "labeling_gold_check",
        "classification_build",
        "classification_list",
        "analysis_backfill_dates",
        "analyze_run",
        # -- analysis method register program (wave 2, tools/*_tools.py) -----
        "analysis_select_method",
        "keyphrases_extract",
        "lexical_classify",
        "neighbors_classify",
        "centroids_classify",
        "clusters_inspect",
        "duplicates_screen",
        "outliers_rank",
        "nli_classify",
        "contradictions_screen",
        "sentiment_screen",
        "active_learn_next",
    }
)


def _auto_approve_errors(prefix: str, auto_approve: object, with_valid_list: bool) -> list[str]:
    """Shared autoApprove rule: a non-empty list of known tool names.

    ``with_valid_list`` adds the sorted allowlist to every unknown-name
    error (the OpenCode rendering); the Claude/Codex rendering names only
    the unknown tool.
    """
    if auto_approve is None:
        return []
    if not isinstance(auto_approve, list):
        return [
            f"{prefix}: 'autoApprove' must be a list of tool names, "
            f"got {type(auto_approve).__name__}"
        ]
    if not auto_approve:
        return [f"{prefix}: 'autoApprove' must not be empty"]
    errors: list[str] = []
    for tool in auto_approve:
        if tool not in VALID_TOOL_NAMES:
            if with_valid_list:
                errors.append(
                    f"{prefix}: invalid tool name in autoApprove: '{tool}' — "
                    f"must be one of: {', '.join(sorted(VALID_TOOL_NAMES))}"
                )
            else:
                errors.append(f"{prefix}: invalid tool name in autoApprove: '{tool}'")
    return errors

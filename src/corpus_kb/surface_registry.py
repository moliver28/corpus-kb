"""Corpus research/coding surface registry — the ONE source of truth (todo 18).

Every research/coding surface gets one row: its CLI subcommand path, its MCP
tool name, the HTTP route where one exists, a one-line description. The
harness command pack (``scripts/gen_harness_pack.py``) is GENERATED from the
WRAPPERS below — never hand-written (r7.2 single-source sync); editing a
generated wrapper without a registry change fails ``--check``.

STRICTLY STDLIB-ONLY: zero ``corpus_kb`` or third-party imports (r9 — the
agent-config-consistency CI job installs NO dependencies, so any import here
is a ModuleNotFoundError the local bar cannot catch). Consumers load this
file by path (``importlib.util.spec_from_file_location``), not as a package
member.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path as _Path
from typing import NamedTuple

# --------------------------------------------------------------------------
# Surfaces: one row per research/coding command pair (CLI + MCP)
# --------------------------------------------------------------------------


class Surface(NamedTuple):
    """One research/coding surface (CLI subcommand + MCP tool).

    NamedTuple (not dataclass) so any file-path loader works WITHOUT
    registering the module in ``sys.modules`` first — dataclass annotation
    resolution requires the module to be importable by name.
    """

    name: str
    cli_path: str
    mcp_tool: str
    description: str
    http_route: str | None = None  # set for every surface outside the parity allowlist
    # The transports this operation declares, as a per-op surface set. The
    # default is full parity (cli+mcp+http); an HTTP-by-nature operation
    # declares the smaller set and stays exempt from the others (ruling 4a).
    surfaces: tuple[str, ...] = ("cli", "mcp", "http")


SURFACES: tuple[Surface, ...] = (
    Surface(
        name="research-ingest-transcript",
        cli_path="research ingest-transcript",
        mcp_tool="research_ingest_transcript",
        description="Ingest ONE transcript (txt/vtt/srt/csv/docx): turns, roles, exchanges.",
        http_route="/api/research/ingest-transcript",
    ),
    Surface(
        name="research-ingest",
        cli_path="research ingest",
        mcp_tool="research_ingest",
        description="Dynamic ingest of file/dir/glob with dedup; --watch tails a drop dir.",
        http_route="/api/research/ingest",
    ),
    Surface(
        name="coding-run",
        cli_path="coding run",
        mcp_tool="coding_run",
        description=(
            "Deductive run: score text against a fixed codebook; "
            "calibrated thresholds; claim sizes."
        ),
        http_route="/api/coding/run",
    ),
    Surface(
        name="coding-inductive",
        cli_path="coding inductive",
        mcp_tool="inductive_run",
        description="Inductive pass: summaries, theme clusters, proposed codes.",
        http_route="/api/coding/inductive",
    ),
    Surface(
        name="codebook-promote",
        cli_path="codebook promote",
        mcp_tool="codebook_promote",
        description="Promote a proposed code into a NEW codebook version (human gate).",
        http_route="/api/codebook/promote",
    ),
    Surface(
        name="research-report",
        cli_path="research report",
        mcp_tool="research_report",
        description=(
            "Governance report: coverage, coding overlap, agreement between coders, gate checks."
        ),
        http_route="/api/research/report",
    ),
    Surface(
        name="review-accept",
        cli_path="review accept",
        mcp_tool="review_execute",
        description="Confirm the model's assignment for one CodingAssignment (human review).",
        http_route="/api/review/execute",
    ),
    Surface(
        name="review-override",
        cli_path="review override",
        mcp_tool="review_execute",
        description="Overrule the model's assignment for one CodingAssignment.",
        http_route="/api/review/execute",
    ),
    Surface(
        name="notebook-ask",
        cli_path="research ask",
        mcp_tool="notebook_ask",
        description="Grounded Q&A: cited sentences; retrieval-only mode = zero LLM calls.",
        http_route="/api/notebook/ask",
    ),
    Surface(
        name="notebook-evidence",
        cli_path="research evidence",
        mcp_tool="notebook_evidence",
        description="Evidence for one code: ranked units with flags + conformal sizes.",
        http_route="/api/notebook/evidence",
    ),
    Surface(
        name="notebook-uncoded",
        cli_path="research uncoded",
        mcp_tool="notebook_uncoded",
        description="Uncoded units ranked by nearest-code margin (the no-missing-clusters radar).",
        http_route="/api/notebook/uncoded",
    ),
    Surface(
        name="notebook-overlap",
        cli_path="research overlap",
        mcp_tool="notebook_overlap",
        description="Code overlap: flagged pairs, confusion, borderline-margin units.",
        http_route="/api/notebook/overlap",
    ),
    Surface(
        name="research-cycle",
        cli_path="research cycle",
        mcp_tool="research_cycle",
        description=(
            "Research cycle: full pipeline with halt gates; long-running, "
            "synchronous on every surface (promotion is human-gated)."
        ),
        http_route="/api/research/cycle",
    ),
    # The narrated demo: every surface runs the same pipeline; the CLI
    # narrates, http/mcp/socket return the receipt envelope (long-running,
    # synchronous — see contracts.catalogue_research).
    Surface(
        name="research-demo",
        cli_path="research demo",
        mcp_tool="research_demo",
        description="Narrated demo pipeline on the bundled demo corpus; long-running.",
        http_route="/api/research/demo",
    ),
    # -- release lifecycle (U5, P7, v6 §6): full cli+mcp+http parity --------
    Surface(
        name="release-assess",
        cli_path="release assess",
        mcp_tool="release_assess",
        description="Compose every release gate over current evidence; WRITES NO RELEASE.",
        http_route="/api/release/assess",
    ),
    Surface(
        name="release-request",
        cli_path="release request",
        mcp_tool="release_request",
        description="Open a draft release pinned to the current codebook content hash.",
        http_route="/api/release/request",
    ),
    Surface(
        name="release-review",
        cli_path="release review",
        mcp_tool="release_review",
        description="Review packet: evidence, failing/not_evaluable gates, waivers, next actions.",
        http_route="/api/release/review",
    ),
    Surface(
        name="release-execute",
        cli_path="release release",
        mcp_tool="release_execute",
        description="Evaluate + record gates, apply waivers, execute the release (approver).",
        http_route="/api/release/release",
    ),
    Surface(
        name="release-status",
        cli_path="release status",
        mcp_tool="release_status",
        description="Stage, release state, blockers, sufficiency flags, the next command.",
        http_route="/api/release/status",
    ),
    Surface(
        name="release-compare",
        cli_path="release compare",
        mcp_tool="release_compare",
        description="Diff two releases' manifests (codes, definitions, methods, metrics).",
        http_route="/api/release/compare",
    ),
    # -- ingest front door (Stage 5): full cli+mcp+http parity ---------------
    Surface(
        name="ingest-status",
        cli_path="ingest-status",
        mcp_tool="ingest_status",
        description="Recent ingest_runs ledger entries + degraded-chunk counts (honest history).",
        http_route="/api/ingest/status",
    ),
    Surface(
        name="ingest-reembed",
        cli_path="ingest-reembed",
        mcp_tool="ingest_reembed",
        description="Re-embed degraded (zero-vector) chunks once the provider is back.",
        http_route="/api/ingest/reembed",
    ),
    Surface(
        name="ingest-reingest",
        cli_path="ingest-reingest",
        mcp_tool="ingest_reingest",
        description="Bulk re-embed for a model switch; dry-run plan, resume, ledger row.",
        http_route="/api/ingest/reingest",
    ),
    # -- pipeline ingest ops (UI SPA Task 5a): the manifest gap closed -------
    Surface(
        name="ingest-directory",
        cli_path="ingest",
        mcp_tool="ingest_directory",
        description="Ingest the supported files under one directory in one walk.",
        http_route="/api/ingest/directory",
    ),
    Surface(
        name="ingest-url",
        cli_path="ingest-url",
        mcp_tool="ingest_url",
        description="Fetch (or crawl) a website and ingest its HTML pages.",
        http_route="/api/ingest/url",
    ),
    # Multipart upload (owner ruling 4a, 2026-11-12): HTTP by nature. The
    # browser posts multipart form data; the JSON side of the SAME pipeline
    # stays ingest-file (CLI/MCP/socket), so this row declares an HTTP-only
    # surface set instead of wrapping the pipeline in a JSON sibling.
    Surface(
        name="ingest-upload",
        cli_path="",
        mcp_tool="",
        description=(
            "Upload one file by multipart form (field name file); the "
            "receipt is the standard file-ingest envelope."
        ),
        http_route="/api/ingest/upload",
        surfaces=("http",),
    ),
    # -- lifecycle UI ops (UI SPA Task 5b, issue #176): demo load + doctor ---
    Surface(
        name="demo-load",
        cli_path="demo-kb",
        mcp_tool="demo_load",
        description="Load the bundled sample corpus through the standard ingest pipeline.",
        http_route="/api/demo/load",
    ),
    Surface(
        name="doctor",
        cli_path="doctor",
        mcp_tool="doctor",
        description="Read-only diagnostics: profile, Postgres, Ollama, env vars, checks.",
        http_route="/api/doctor",
    ),
    # -- lifecycle read lists (UI SPA Task 5b, spec B2): the GET rows -------
    Surface(
        name="projects-list",
        cli_path="research projects",
        mcp_tool="projects_list",
        description="List research projects, newest first (read-only).",
        http_route="/api/projects",
    ),
    Surface(
        name="codebooks-list",
        cli_path="codebook list",
        mcp_tool="codebooks_list",
        description="List codebook versions with labels, hashes, and dates (read-only).",
        http_route="/api/codebooks",
    ),
    Surface(
        name="codebook-proposals",
        cli_path="codebook proposals",
        mcp_tool="codebook_proposals",
        description="List proposed codes for the promote gate (read-only).",
        http_route="/api/codebook/proposals",
    ),
    Surface(
        name="coding-assignments",
        cli_path="coding assignments",
        mcp_tool="coding_assignments",
        description="List coding assignments for the review queue, with filters (read-only).",
        http_route="/api/coding/assignments",
    ),
    # -- topics layer (Wave 4): full cli+mcp+http parity ----------------------
    Surface(
        name="topics-build",
        cli_path="topics-build",
        mcp_tool="topics_build",
        description="Cluster stored chunk embeddings into topics (offline BERTopic).",
        http_route="/api/topics/build",
    ),
    Surface(
        name="topics-list",
        cli_path="topics-list",
        mcp_tool="topics_list",
        description="List the built topics with sizes, labels, and keywords.",
        http_route="/api/topics",
    ),
    # -- analysis program (Wave 0 skeleton): full cli+mcp+http parity --------
    Surface(
        name="topics-trends",
        cli_path="topics-trends",
        mcp_tool="topics_trends",
        description="Per-topic frequency series over binned document dates.",
        http_route="/api/topics/trends",
    ),
    Surface(
        name="labeling-build",
        cli_path="labeling-build",
        mcp_tool="labeling_build",
        description="Score the labeling functions and write the aggregated chunk labels.",
        http_route="/api/labeling/build",
    ),
    Surface(
        name="labeling-list",
        cli_path="labeling-list",
        mcp_tool="labeling_list",
        description="List the built chunk labels with probabilities.",
        http_route="/api/labeling",
    ),
    Surface(
        name="labeling-lfs",
        cli_path="labeling-lfs",
        mcp_tool="labeling_lfs",
        description="List the registered labeling functions with kinds and coverage.",
        http_route="/api/labeling/lfs",
    ),
    Surface(
        name="labeling-triage",
        cli_path="labeling-triage",
        mcp_tool="labeling_triage",
        description="Rank built labels with weak support into a human review queue.",
        http_route="/api/labeling/triage",
    ),
    Surface(
        name="labeling-gold-check",
        cli_path="labeling-gold-check",
        mcp_tool="labeling_gold_check",
        description="Score built labels against a gold dev set (PPI-corrected).",
        http_route="/api/labeling/gold-check",
    ),
    Surface(
        name="classification-build",
        cli_path="classification-build",
        mcp_tool="classification_build",
        description="Fit the few-shot classifier against a seed set; write predictions.",
        http_route="/api/classification/build",
    ),
    Surface(
        name="classification-list",
        cli_path="classification-list",
        mcp_tool="classification_list",
        description="List the built chunk predictions, bounded by limit.",
        http_route="/api/classification",
    ),
    Surface(
        name="analysis-backfill-dates",
        cli_path="analysis-backfill-dates",
        mcp_tool="analysis_backfill_dates",
        description="Backfill documents.document_date from chunk metadata (idempotent).",
        http_route="/api/analysis/backfill-dates",
    ),
    Surface(
        name="analyze-run",
        cli_path="analyze-run",
        mcp_tool="analyze_run",
        description="Run the composed corpus analysis and write the report.",
        http_route="/api/analyze",
    ),
    # -- analysis method register program (wave 2): full cli+mcp+http --------
    Surface(
        name="analysis-select-method",
        cli_path="analysis-select-method",
        mcp_tool="analysis_select_method",
        description="Rank the register's methods for a stated task (pure registry logic).",
        http_route="/api/analysis/select-method",
    ),
    # -- analysis question-routing program (wave 3): the question front door -
    Surface(
        name="analysis-route",
        cli_path="analysis-route",
        mcp_tool="analysis_route",
        description="Route a plain-language question to the right analysis method.",
        http_route="/api/analysis/route",
    ),
    Surface(
        name="keyphrases-extract",
        cli_path="keyphrases-extract",
        mcp_tool="keyphrases_extract",
        description="Rank keyphrase candidates (tfidf, embedding, or llm variant).",
        http_route="/api/analysis/keyphrases-extract",
    ),
    Surface(
        name="lexical-classify",
        cli_path="lexical-classify",
        mcp_tool="lexical_classify",
        description="TF-IDF + logistic labels trained on the stored weak labels.",
        http_route="/api/analysis/lexical-classify",
    ),
    Surface(
        name="neighbors-classify",
        cli_path="neighbors-classify",
        mcp_tool="neighbors_classify",
        description="kNN vote labels over the labeled chunks, neighbors listed.",
        http_route="/api/analysis/neighbors-classify",
    ),
    Surface(
        name="centroids-classify",
        cli_path="centroids-classify",
        mcp_tool="centroids_classify",
        description="Nearest-centroid labels with recorded abstentions.",
        http_route="/api/analysis/centroids-classify",
    ),
    Surface(
        name="clusters-inspect",
        cli_path="clusters-inspect",
        mcp_tool="clusters_inspect",
        description="Cluster structure report: sizes, noise, exemplar chunk ids.",
        http_route="/api/analysis/clusters-inspect",
    ),
    Surface(
        name="duplicates-screen",
        cli_path="duplicates-screen",
        mcp_tool="duplicates_screen",
        description="Near-duplicate candidate pairs at a cosine threshold.",
        http_route="/api/analysis/duplicates-screen",
    ),
    Surface(
        name="outliers-rank",
        cli_path="outliers-rank",
        mcp_tool="outliers_rank",
        description="Chunks ranked by distance to the k-th nearest neighbor.",
        http_route="/api/analysis/outliers-rank",
    ),
    Surface(
        name="nli-classify",
        cli_path="nli-classify",
        mcp_tool="nli_classify",
        description="Zero-shot labels via entailment judgments per hypothesis.",
        http_route="/api/analysis/nli-classify",
    ),
    Surface(
        name="contradictions-screen",
        cli_path="contradictions-screen",
        mcp_tool="contradictions_screen",
        description="Candidate pairs the NLI model marks CONTRADICT, for human review.",
        http_route="/api/analysis/contradictions-screen",
    ),
    Surface(
        name="sentiment-screen",
        cli_path="sentiment-screen",
        mcp_tool="sentiment_screen",
        description="Preset lexicon sentiment screen (vote aggregation, honest note).",
        http_route="/api/analysis/sentiment-screen",
    ),
    Surface(
        name="active-learn-next",
        cli_path="active-learn-next",
        mcp_tool="active_learn_next",
        description="Rank stored predictions for the next human-labeling batch.",
        http_route="/api/analysis/active-learn-next",
    ),
    Surface(
        name="survey-openend-codes",
        cli_path="survey-openend-codes",
        mcp_tool="survey_openend_codes",
        description="Code open-ended survey answers per question (cluster, label, count).",
        http_route="/api/analysis/survey-openend-codes",
    ),
    Surface(
        name="usability-severity",
        cli_path="usability-severity",
        mcp_tool="usability_severity",
        description="Score findings with the fixed 0-4 severity rubric.",
        http_route="/api/analysis/usability-severity",
    ),
    Surface(
        name="emotion-tag",
        cli_path="emotion-tag",
        mcp_tool="emotion_tag",
        description="Tag chunks with the fixed eight-emotion set via entailment.",
        http_route="/api/analysis/emotion-tag",
    ),
    # -- core search op (the last surface it was missing): full cli+mcp+http --
    Surface(
        name="search",
        cli_path="search",
        mcp_tool="search",
        description="Hybrid vector + full-text search with the degraded envelope.",
        http_route="/api/search",
    ),
    # -- ask operation (UX program Wave 1): full cli+mcp+http parity ----------
    Surface(
        name="ask",
        cli_path="ask",
        mcp_tool="ask",
        description="Answer a question from retrieved evidence with cited sentences.",
        http_route="/api/ask",
    ),
    Surface(
        name="queue-enqueue",
        cli_path="queue-enqueue",
        mcp_tool="queue_enqueue",
        description="Queue one file for ingestion; repeat calls answer the existing row.",
        http_route="/api/queue/enqueue",
    ),
    Surface(
        name="queue-status",
        cli_path="queue-status",
        mcp_tool="queue_status",
        description="Show one queue row, or the pending and running rows.",
        http_route="/api/queue/status",
    ),
    Surface(
        name="queue-cancel",
        cli_path="queue-cancel",
        mcp_tool="queue_cancel",
        description="Cancel one pending or running queue row.",
        http_route="/api/queue/cancel",
    ),
    # -- Track C: the per-project raw-table ops. -------------------------
    Surface(
        name="project-provision",
        cli_path="project-provision",
        mcp_tool="project_provision",
        description="Create the raw table for one project slug (idempotent).",
        http_route="/api/project/provision",
    ),
    Surface(
        name="project-list",
        cli_path="project-list",
        mcp_tool="project_list",
        description="List provisioned project raw tables with row counts.",
        http_route="/api/project/list",
    ),
    Surface(
        name="project-drop",
        cli_path="project-drop",
        mcp_tool="project_drop",
        description="Drop one project raw table and its registry row.",
        http_route="/api/project/drop",
    ),
    # -- UI manifest self-upgrade reads (issue #182): the Review and Ask
    #    screens resolve these ids through getOp; the names follow the
    #    manifest ids the shipped screens already reference. --------------
    Surface(
        name="documents-list",
        cli_path="list-documents",
        mcp_tool="list_documents",
        description="List ingested documents with chunk counts (read-only).",
        http_route="/api/documents",
    ),
    Surface(
        name="document-delete",
        cli_path="delete-document",
        mcp_tool="delete_document",
        description="Delete one document by ID; chunks and vectors cascade.",
        http_route="/api/documents/{doc_id}",
    ),
    Surface(
        name="document-tags",
        cli_path="get-document-tags",
        mcp_tool="get_document_tags",
        description="List one document's tags (read-only).",
        http_route="/api/documents/{doc_id}/tags",
    ),
    Surface(
        name="document-metadata",
        cli_path="get-metadata",
        mcp_tool="get_metadata",
        description="Read metadata rows by key or document (read-only).",
        http_route="/api/metadata",
    ),
    Surface(
        name="verify",
        cli_path="verify-answer",
        mcp_tool="verify_answer",
        description="Check each answer claim against the cited chunks.",
        http_route="/api/verify",
    ),
    Surface(
        name="search-similar",
        cli_path="search-similar",
        mcp_tool="search_similar",
        description="Find chunks similar to one stored chunk (vector distance).",
        http_route="/api/search/similar",
    ),
    Surface(
        name="search-context",
        cli_path="search-context",
        mcp_tool="search_context",
        description="Search with the surrounding context chunks attached.",
        http_route="/api/search/context",
    ),
    # -- calibrate family (issue #200): apply a profile lock's overrides ----
    # Write op: NEVER add it to SERVER_AUTO_APPROVE.
    Surface(
        name="calibrate-apply",
        cli_path="calibrate apply",
        mcp_tool="calibrate_apply",
        description="Copy a profile lock's proposed overrides into config.yaml.",
        http_route="/api/calibrate/apply",
    ),
    # -- chunk-coding ops (issue #350): the six routes the plugin reached
    #    only by hard-coded HTTP posts, plus the two reads it built by
    #    hand (status from four SQL queries, export from hand-written SQL).
    #    The three embedding/pooling/batching rows are long ops (minutes
    #    on a real corpus; the UI manifest pins the same ids). -----------
    Surface(
        name="coding-embed-codebook",
        cli_path="coding embed-codebook",
        mcp_tool="coding_embed_codebook",
        description="Validate, embed, and load one codebook; answers its version id.",
        http_route="/api/coding/embed-codebook",
    ),
    Surface(
        name="coding-pool",
        cli_path="coding pool",
        mcp_tool="coding_pool",
        description="Run pooling (keyword hits, similarity) and coverage reconciliation.",
        http_route="/api/coding/pool",
    ),
    Surface(
        name="coding-code-batch",
        cli_path="coding code-batch",
        mcp_tool="coding_code_batch",
        description="Code a batch of (chunk, code) cells with confidence routing.",
        http_route="/api/coding/code-batch",
    ),
    Surface(
        name="coding-reliability",
        cli_path="coding reliability",
        mcp_tool="coding_reliability",
        description="Sample the coded corpus and compute alpha/kappa reliability.",
        http_route="/api/coding/reliability",
    ),
    Surface(
        name="coding-calibrate-floors",
        cli_path="coding calibrate-floors",
        mcp_tool="coding_calibrate_floors",
        description="Calibrate one code's pool/residual floors from labeled chunks.",
        http_route="/api/coding/calibrate-floors",
    ),
    Surface(
        name="coding-saturation",
        cli_path="coding saturation",
        mcp_tool="coding_saturation",
        description="Report the ISR saturation signal and the batch stop rule.",
        http_route="/api/coding/saturation",
    ),
    Surface(
        name="coding-status",
        cli_path="coding status",
        mcp_tool="coding_status",
        description="Corpus counts, saturation ratio, coverage by disposition, routes.",
        http_route="/api/coding/status",
    ),
    Surface(
        name="coding-export",
        cli_path="coding export",
        mcp_tool="coding_export",
        description="Accepted codes joined with chunk text and document source, as rows.",
        http_route="/api/coding/export",
    ),
    # -- event-store health (issue #350): one op replaces the plugin's four
    #    SQL queries and its direct read of the DuckDB events sidecar. ----
    Surface(
        name="events-summary",
        cli_path="events",
        mcp_tool="events_summary",
        description=(
            "Event counts by topic, latest notification id, checkpoints, "
            "dead letters, recent ingest runs (read-only)."
        ),
        http_route="/api/events/summary",
    ),
)

# --------------------------------------------------------------------------
# Harness wrappers: the generated command pack
# --------------------------------------------------------------------------

# The wrapper bodies live in surface_wrappers.py (also stdlib-only). This
# file loads it by path, like its own consumers do, so the zero-import
# rule here stays true.

_wrappers_spec = importlib.util.spec_from_file_location(
    "corpus_kb._surface_wrappers", _Path(__file__).with_name("surface_wrappers.py")
)
assert _wrappers_spec is not None and _wrappers_spec.loader is not None
_wrappers_mod = importlib.util.module_from_spec(_wrappers_spec)
_wrappers_spec.loader.exec_module(_wrappers_mod)

Wrapper = _wrappers_mod.Wrapper
WRAPPERS = _wrappers_mod.WRAPPERS

ACTIVE_WRAPPERS: tuple[Wrapper, ...] = tuple(w for w in WRAPPERS if not w.planned)

# Description MUST byte-match the existing mcp-configs/*.json entries
# (check_agent_configs compares descriptions across editors).
SERVER_NAME = "corpus-kb"
SERVER_DESCRIPTION = (
    "Local knowledge base for files, transcripts, and code. "
    "Search, ingest, and analyze on your own machine."
)
SERVER_COMMAND = "corpus-kb"
SERVER_ARGS: tuple[str, ...] = ("--transport", "stdio")
# autoApprove for the emitted editor templates (novice-UX W3c): ONE
# registry-derived set, not a hand list. The rule: a tool is
# auto-approvable only when it performs NO store write and NO ingest
# write under ANY declared parameter. ``AUTO_APPROVE_WRITE_TOOLS`` names
# the write side; the derived list is every MCP tool minus those rows,
# sorted. The lockstep test (``tests/test_auto_approve_lockstep.py``)
# recomputes the universe from the catalogue plus this registry and
# fails when a new op is not classified into exactly one of the two
# sets. Two judgment calls the rule allows: ``coding_export`` and
# ``research_report`` write a file the caller names — no store row, no
# ingest — so they ride. The legacy master-tool names (``sql_query``,
# ``retrieve_context``, and friends) are no longer live MCP tools, so
# no host list carries them.
AUTO_APPROVE_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # ingest writes: documents, chunks, vectors, entities, relations
        "ingest_text",
        "ingest_file",
        "ingest_directory",
        "ingest_url",
        "research_ingest",
        "research_ingest_transcript",
        "ingest_reembed",
        "ingest_reingest",
        "research_cycle",
        "research_demo",
        "demo_load",
        # store writes: rows, tables, graph, queue, lifecycle decisions
        "delete_document",
        "add_entity",
        "add_relation",
        "extraction_tune",
        "conflicts_scan",
        "conflicts_resolve",
        "beliefs_current",
        "labeling_build",
        "classification_build",
        "topics_build",
        "topics_trends",
        "analysis_backfill_dates",
        "analyze_run",
        "analysis_embed_meta",
        "project_provision",
        "project_drop",
        "queue_enqueue",
        "queue_cancel",
        "codebook_promote",
        "coding_embed_codebook",
        "coding_code_batch",
        "coding_calibrate_floors",
        "coding_run",
        "inductive_run",
        "review_execute",
        "calibrate_apply",
        "release_request",
        "release_review",
        "release_execute",
    }
)

SERVER_AUTO_APPROVE: tuple[str, ...] = (
    "active_learn_next",
    "analysis_route",
    "analysis_select_method",
    "ask",
    "beliefs_compare",
    "beliefs_predict",
    "beliefs_timeline",
    "centroids_classify",
    "classification_list",
    "clusters_inspect",
    "codebook_proposals",
    "codebooks_list",
    "coding_assignments",
    "coding_export",
    "coding_pool",
    "coding_reliability",
    "coding_saturation",
    "coding_status",
    "conflicts_list",
    "contradictions_screen",
    "doctor",
    "duplicates_screen",
    "emotion_tag",
    "events_summary",
    "get_document_tags",
    "get_entity_relations",
    "get_metadata",
    "graph_neighbors",
    "graph_traverse",
    "ingest_status",
    "keyphrases_extract",
    "labeling_gold_check",
    "labeling_lfs",
    "labeling_list",
    "labeling_triage",
    "lexical_classify",
    "list_documents",
    "neighbors_classify",
    "nli_classify",
    "notebook_ask",
    "notebook_evidence",
    "notebook_overlap",
    "notebook_uncoded",
    "outliers_rank",
    "project_list",
    "projects_list",
    "query",
    "queue_status",
    "release_assess",
    "release_compare",
    "release_status",
    "research_report",
    "search",
    "search_context",
    "search_graph",
    "search_similar",
    "sentiment_screen",
    "statements_list",
    "statements_timeline",
    "survey_openend_codes",
    "topics_list",
    "usability_severity",
    "verify_answer",
)

# Marker fences for the generated .claude/CLAUDE.md research-commands section.
CLAUDEMD_START = "<!-- corpus-harness:research-commands:start -->"
CLAUDEMD_END = "<!-- corpus-harness:research-commands:end -->"


def claudemd_section() -> str:
    """Render the research-commands section injected into .claude/CLAUDE.md."""
    lines = [
        CLAUDEMD_START,
        "## Research command pack (generated — do not edit)",
        "",
        "Every research/coding surface, generated from `src/corpus_kb/surface_registry.py`",
        "by `scripts/gen_harness_pack.py`. Bodies live in `.claude/commands/` and",
        "`.opencode/command/`; editing a wrapper without a registry change fails",
        "`scripts/gen_harness_pack.py --check`.",
        "",
        "| Harness command | CLI path | MCP tool | Purpose |",
        "|-----------------|----------|----------|---------|",
    ]
    surface_by_name = {s.name: s for s in SURFACES}
    for wrapper in ACTIVE_WRAPPERS:
        for surface_name in wrapper.covers:
            surface = surface_by_name[surface_name]
            tool = f"`{surface.mcp_tool}`" if surface.mcp_tool else "(none)"
            lines.append(
                f"| `{wrapper.name}` | `corpus-kb {surface.cli_path}` "
                f"| {tool} | {surface.description} |"
            )
    planned = [w for w in WRAPPERS if w.planned]
    if planned:
        names = ", ".join(f"`{w.name}`" for w in planned)
        lines += ["", f"Planned wrappers (not emitted yet): {names}."]
    lines += [CLAUDEMD_END, ""]
    return "\n".join(lines)

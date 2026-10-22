"""The operation catalogue: the analysis family (analysis program W0 + AMR).

``analysis_backfill_dates`` is the one-shot date-provenance backfill
(WP-1: chunks metadata -> ``documents.document_date``, the migration 027
rule, callable after new ingests). ``analyze_run`` is the composed
corpus analysis (WP-5): date coverage + labeling + classification +
trends into one deterministic, snapshot-able report.

The twelve AMR rows (analysis method register program, wave 2) register
the register-backed methods: the selector plus the keyphrase, lexical,
vector-space, NLI, sentiment, and active-learning ops of wave 1. Their
handlers are real; the honest guard paths (``disabled``/``unavailable``/
``empty``) carry the same contract keys. ``clusters_inspect`` also
returns a ``params`` record on success (the reduction that ran, the
random state, the resolved minimum) — declared per surface through
``http_extra``/``socket_extra``. Registry-first rule: change the row
first, then the surfaces, then the gates. All strings are plain English
(ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

# Guaranteed-on-success keys only: the disabled/unavailable reasons stay
# off the tuple (the envelopes.py rule).
BACKFILL_ENVELOPE = ("status", "documents_scanned", "documents_dated")
ANALYZE_ENVELOPE = ("status", "sections")

# AMR envelopes: guaranteed-on-success keys only (the envelopes.py rule).
SELECT_ENVELOPE = ("status", "register_version", "recommendations")
KEYPHRASES_ENVELOPE = ("status", "keyphrases", "variant", "screened_count")
PREDICTIONS_ENVELOPE = ("status", "predictions", "screened_count")
NLI_PREDICTIONS_ENVELOPE = ("status", "predictions", "model", "screened_count")
CLUSTERS_ENVELOPE = ("status", "clusters", "noise_count", "screened_count")
DUPLICATES_ENVELOPE = ("status", "pairs", "threshold", "screened_count")
OUTLIERS_ENVELOPE = ("status", "outliers", "screened_count")
CONTRADICTIONS_ENVELOPE = ("status", "pairs", "screened_count")
RANKED_ENVELOPE = ("status", "ranked", "strategy", "requested_batch")

ANALYSIS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="analysis_backfill_dates",
        family="analysis",
        description=(
            "Backfill documents.document_date from chunk metadata (the "
            "migration 027 rule, idempotent). Gated behind "
            "analysis.topics.over_time.enabled."
        ),
        handler="corpus_kb.tools.analysis_tools:analysis_backfill_dates",
        params=(_TENANT,),
        envelope_keys=BACKFILL_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "analysis_backfill_dates"),
            SurfaceSpec(CLI, "analysis-backfill-dates"),
            SurfaceSpec(HTTP, "/api/analysis/backfill-dates", "POST"),
            SurfaceSpec(SOCKET, "analysis_backfill_dates"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="analyze_run",
        family="analysis",
        description=(
            "Run the composed corpus analysis (dates, labels, "
            "predictions, trends) and write the report. Honest no-op "
            "while every analysis flag is false."
        ),
        handler="corpus_kb.tools.analysis_tools:analyze_run",
        params=(ParamSpec("output", default=None), _TENANT),
        envelope_keys=ANALYZE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "analyze_run"),
            SurfaceSpec(CLI, "analyze-run"),
            SurfaceSpec(HTTP, "/api/analyze", "POST"),
            SurfaceSpec(SOCKET, "analyze_run"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    # -- AMR program wave 2: the register-backed method ops -----------------
    Operation(
        name="analysis_select_method",
        family="analysis",
        description=(
            "Rank the register's methods for a stated task (output "
            "family, inputs, complexity cap). Pure registry logic; no LLM."
        ),
        handler="corpus_kb.tools.method_select_tools:analysis_select_method",
        params=(
            ParamSpec("task_description", required=True),
            ParamSpec("output_family", default=None),
            ParamSpec("inputs_available", default=None),
            ParamSpec("max_complexity_tier", default=None),
            _TENANT,
        ),
        envelope_keys=SELECT_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "analysis_select_method"),
            SurfaceSpec(CLI, "analysis-select-method"),
            SurfaceSpec(HTTP, "/api/analysis/select-method", "POST"),
            SurfaceSpec(SOCKET, "analysis_select_method"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="keyphrases_extract",
        family="analysis",
        description=(
            "Rank keyphrase candidates over selected chunks (tfidf, "
            "embedding, or llm variant). Register methods 05/06/07."
        ),
        handler="corpus_kb.tools.keyphrase_tools:keyphrases_extract",
        params=(
            ParamSpec("variant", default="tfidf"),
            ParamSpec("limit", default=20),
            ParamSpec("chunk_ids", default=None),
            _TENANT,
        ),
        envelope_keys=KEYPHRASES_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "keyphrases_extract"),
            SurfaceSpec(CLI, "keyphrases-extract"),
            SurfaceSpec(HTTP, "/api/analysis/keyphrases-extract", "POST"),
            SurfaceSpec(SOCKET, "keyphrases_extract"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="lexical_classify",
        family="analysis",
        description=(
            "Train TF-IDF + logistic head on the stored weak-supervision "
            "labels and score the corpus. Register method 15."
        ),
        handler="corpus_kb.tools.lexical_tools:lexical_classify",
        params=(ParamSpec("chunk_ids", default=None), _TENANT),
        envelope_keys=PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "lexical_classify"),
            SurfaceSpec(CLI, "lexical-classify"),
            SurfaceSpec(HTTP, "/api/analysis/lexical-classify", "POST"),
            SurfaceSpec(SOCKET, "lexical_classify"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="neighbors_classify",
        family="analysis",
        description=(
            "kNN vote over the labeled chunks; labels for the requested chunks. Register method 14."
        ),
        handler="corpus_kb.tools.neighbor_tools:neighbors_classify",
        params=(
            ParamSpec("k", default=5),
            ParamSpec("chunk_ids", default=None),
            _TENANT,
        ),
        envelope_keys=PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "neighbors_classify"),
            SurfaceSpec(CLI, "neighbors-classify"),
            SurfaceSpec(HTTP, "/api/analysis/neighbors-classify", "POST"),
            SurfaceSpec(SOCKET, "neighbors_classify"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="centroids_classify",
        family="analysis",
        description=(
            "Nearest-centroid labels with recorded abstentions at the margin. Register method 13."
        ),
        handler="corpus_kb.tools.centroid_tools:centroids_classify",
        params=(
            ParamSpec("abstain_margin", default=0.05),
            ParamSpec("chunk_ids", default=None),
            _TENANT,
        ),
        envelope_keys=PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "centroids_classify"),
            SurfaceSpec(CLI, "centroids-classify"),
            SurfaceSpec(HTTP, "/api/analysis/centroids-classify", "POST"),
            SurfaceSpec(SOCKET, "centroids_classify"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="clusters_inspect",
        family="analysis",
        description=(
            "Reduce the stored vectors, cluster them, and report sizes, "
            "noise, and exemplars. Register method 12."
        ),
        handler="corpus_kb.tools.cluster_tools:clusters_inspect",
        params=(
            ParamSpec("min_cluster_size", default=None),
            ParamSpec("sample_cap", default=None),
            _TENANT,
        ),
        envelope_keys=CLUSTERS_ENVELOPE,
        http_extra=frozenset({"params"}),
        socket_extra=frozenset({"params"}),
        surfaces=(
            SurfaceSpec(MCP, "clusters_inspect"),
            SurfaceSpec(CLI, "clusters-inspect"),
            SurfaceSpec(HTTP, "/api/analysis/clusters-inspect", "POST"),
            SurfaceSpec(SOCKET, "clusters_inspect"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="duplicates_screen",
        family="analysis",
        description=(
            "Cosine pairs at or above a threshold; near-duplicate candidates. Register method 29."
        ),
        handler="corpus_kb.tools.vector_screen_tools:duplicates_screen",
        params=(
            ParamSpec("threshold", default=0.98),
            ParamSpec("limit", default=100),
            _TENANT,
        ),
        envelope_keys=DUPLICATES_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "duplicates_screen"),
            SurfaceSpec(CLI, "duplicates-screen"),
            SurfaceSpec(HTTP, "/api/analysis/duplicates-screen", "POST"),
            SurfaceSpec(SOCKET, "duplicates_screen"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="outliers_rank",
        family="analysis",
        description=("Rank chunks by distance to the k-th nearest neighbor. Register method 30."),
        handler="corpus_kb.tools.vector_screen_tools:outliers_rank",
        params=(
            ParamSpec("k", default=10),
            ParamSpec("limit", default=20),
            _TENANT,
        ),
        envelope_keys=OUTLIERS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "outliers_rank"),
            SurfaceSpec(CLI, "outliers-rank"),
            SurfaceSpec(HTTP, "/api/analysis/outliers-rank", "POST"),
            SurfaceSpec(SOCKET, "outliers_rank"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="nli_classify",
        family="analysis",
        description=(
            "Zero-shot labels via entailment judgments per hypothesis. Register method 18."
        ),
        handler="corpus_kb.tools.nli_tools:nli_classify",
        params=(
            ParamSpec("labels", required=True),
            ParamSpec("chunk_ids", default=None),
            ParamSpec("limit", default=50),
            ParamSpec("model", default=None),
            _TENANT,
        ),
        envelope_keys=NLI_PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "nli_classify"),
            SurfaceSpec(CLI, "nli-classify"),
            SurfaceSpec(HTTP, "/api/analysis/nli-classify", "POST"),
            SurfaceSpec(SOCKET, "nli_classify"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="contradictions_screen",
        family="analysis",
        description=(
            "Judge vector-prefiltered candidate pairs; report the "
            "CONTRADICT verdicts. Register method 28."
        ),
        handler="corpus_kb.tools.nli_tools:contradictions_screen",
        params=(
            ParamSpec("candidate_k", default=5),
            ParamSpec("limit", default=50),
            ParamSpec("model", default=None),
            _TENANT,
        ),
        envelope_keys=CONTRADICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "contradictions_screen"),
            SurfaceSpec(CLI, "contradictions-screen"),
            SurfaceSpec(HTTP, "/api/analysis/contradictions-screen", "POST"),
            SurfaceSpec(SOCKET, "contradictions_screen"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="sentiment_screen",
        family="analysis",
        description=(
            "Preset lexicon screen through the labeling engine; vote "
            "aggregation. Register methods 26 and 27."
        ),
        handler="corpus_kb.tools.sentiment_tools:sentiment_screen",
        params=(
            ParamSpec("chunk_ids", default=None),
            ParamSpec("limit", default=None),
            _TENANT,
        ),
        envelope_keys=PREDICTIONS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "sentiment_screen"),
            SurfaceSpec(CLI, "sentiment-screen"),
            SurfaceSpec(HTTP, "/api/analysis/sentiment-screen", "POST"),
            SurfaceSpec(SOCKET, "sentiment_screen"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="active_learn_next",
        family="analysis",
        description=(
            "Rank stored predictions for the next human-labeling batch "
            "(margin, entropy, random_incumbent). Register method 20."
        ),
        handler="corpus_kb.tools.active_learn_tools:active_learn_next",
        params=(
            ParamSpec("strategy", default="margin"),
            ParamSpec("batch", default=10),
            _TENANT,
        ),
        envelope_keys=RANKED_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "active_learn_next"),
            SurfaceSpec(CLI, "active-learn-next"),
            SurfaceSpec(HTTP, "/api/analysis/active-learn-next", "POST"),
            SurfaceSpec(SOCKET, "active_learn_next"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)

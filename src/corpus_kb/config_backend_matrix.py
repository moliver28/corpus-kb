"""Config-leaf x backend declaration matrix (parity program PR-9, rule 12).

ONE declarative row per config leaf in ``get_default_config()`` and the
packaged ``config.yaml``. ``scripts/config_backend_parity_gate.py`` fails
when either source and this table disagree, when a reader file lacks its
grep token (the dead-key check), or when a refusal row lost its binding
test. The five PR-7-removed keys are absent BY CONSTRUCTION.

STRICTLY DATA + STDLIB: the PyYAML-only CI job and doctor load this file by
path. The op half lives in ``ops_backend_matrix.py``. ``_RAW`` records are
``keys|behavior|reader|note|token|bind`` (``keys``: whitespace group of leaves).
"""

from __future__ import annotations

from typing import NamedTuple

POSTGRES = ("postgres",)
DUCKDB = ("duckdb",)
BOTH = ("postgres", "duckdb")


class ConfigBackendRow(NamedTuple):
    """One config leaf's backend declaration (see module docstring)."""

    key: str
    backends: tuple[str, ...]
    behavior: str
    reader: str
    note: str
    token: str = ""
    bind: str = ""


_RAW: list[str] = [
    "analysis.method_register_path analysis.classification.enabled "
    "analysis.classification.min_labels_per_class analysis.classification.tier "
    "analysis.eecs.enabled analysis.labeling.enabled analysis.labeling.lfs_file "
    "analysis.labeling.min_signals analysis.routing.fallback_log_path "
    "analysis.routing.llm_fallback_enabled analysis.sentiment.lexicon_path "
    "analysis.topics.max_embeddings analysis.topics.over_time.bins "
    "analysis.topics.over_time.enabled analysis.topics.over_time.undated|honored|"
    "analysis/settings.py|Typed loader; gates date backfill||",
    "analysis.routing.confidence_threshold analysis.routing.margin routing.confidence_threshold|"
    "honored|eval/scoring.py|||",
    "analysis.sentiment.enabled|honored|tools/sentiment_tools.py|Sentiment gate||",
    "ask.max_sources|honored|ask/ask_settings.py|Ask candidate cap||",
    "cache.deductive_enabled cache.enabled|honored|research/deductive_run.py|Deductive-run cache "
    "gates||",
    "cache.inductive_enabled|honored|research/exact_cache.py|Inductive exact-cache gate|"
    "inductive_enabled|",
    "coding.coder|honored|coding/coder_dispatch.py|Coder pick||",
    'coding.model|honored|server_wiring.py||coding_cfg.get("model"|',
    'llm.base_url|honored|handlers/llm_handler.py||_str_or_default(llm, "base_url"|',
    'llm.model|honored|api/routes_v1.py||llm_section.get("model"|',
    "coding.repair_retries|honored|coding/structured_output.py|Repair loop||",
    "coding.require_structured_enforcement|honored|config_defaults_features.py||require_structured_enforcement|",
    "database.backend database.connection_string routing.enabled retrieval.self_query.enabled "
    "retrieval.self_query.model retrieval.self_query.base_url retrieval.self_query.timeout_seconds "
    "server.host|honored|server_wiring.py|Engine, DSN, router, self-query, host|self_query|",
    "embedding.base_url embedding.batch_size embedding.max_batch_texts embedding.query_instruction|"
    "honored|rag/embedder.py||base_url|",
    "embedding.dimensions embedding.late_chunking embedding.model|honored|"
    "rag/embedder_selection.py|||",
    "embedding.fallback_provider|honored|tools/ingest_embed.py|Fallback pick||",
    "embedding.mrl_trained|honored|config_gates.py|U32 width gate||",
    "embedding.provider|honored|rag/embedder_factory.py|Pick; pgml refuses on duckdb||",
    "extraction.backend graph.extractor extraction.ensemble.enabled extraction.ensemble.members|"
    "honored|extraction/__init__.py||ensemble|",
    "extraction.mode graph.fixture_dir graph.live_fallback|honored|extraction/ml_backend.py|ML "
    "mode gate; fixture escape hatch|fixture_dir|",
    "extraction.cache.enabled|honored|_setup/validate_extraction_ml.py|||",
    "extraction.gliner.enabled extraction.gliner.model extraction.gliner.revision "
    "extraction.gliner.types|honored|extraction/gliner_backend.py|GLiNER2 NER settings|gliner|",
    "extraction.relex.max_label_pairs extraction.relex.model extraction.relex.threshold|honored|"
    "extraction/relex_backend.py|GLiNER-Relex settings|relex|",
    "extraction.tune.batch_size extraction.tune.model extraction.tune.steps "
    "extraction.tune.learning_rate extraction.tune.lora_alpha extraction.tune.lora_dropout "
    "extraction.tune.lora_r|honored|extraction/lora_tune.py|LoRA tuning knobs|learning_rate|",
    "graph.backend|both-with-mix-rules|backend_gates.py|Postgres-under-duck refuses (G09)||"
    "GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR",
    "graph.duckdb_graph_backend|duckdb-only|backend_gates.py|Tuned value refuses under postgres||"
    "DUCKDB_GRAPH_BACKEND_ON_POSTGRES_ERROR",
    "graph.max_hops|honored|handlers/graph_handler.py|||",
    "graph.model_version graph.prompt_version|honored|tools/ingest_store.py|||",
    "graph.ontology_path ingest.contextualize.enabled ingest.contextualize.enabled_source_types|"
    "honored|tools/ingest_common.py||ontology_path|",
    "graph_policy.mode|honored|storage/graph_policy.py|Write policy||",
    "ingest.chunking.enabled ingest.chunking.child_tokens ingest.chunking.chunk_tokens "
    "ingest.chunking.parent_tokens ingest.chunking.overlap_tokens "
    "ingest.chunking.respect_boundaries|honored|chunking/hierarchy_settings.py||child_tokens|",
    "ingest.contextualize.backend ingest.contextualize.model ingest.contextualize.prompt_profile "
    "ingest.contextualize.max_context_tokens ingest.contextualize.document_first_prompt "
    "ingest.contextualize.apply_to ingest.contextualize.base_url ingest.contextualize.fixture_dir "
    "ingest.contextualize.temperature ingest.contextualize.live_fallback|honored|"
    "rag/contextual_settings.py||document_first_prompt|",
    "ingestion.concurrency|honored-both-serial-by-design-on-duck|tools/ingest_common.py|R8: serial "
    "by design on duckdb||",
    "ingestion.default ingestion.strategies.survey|honored|ingestion/registry.py||survey|",
    "ingestion.strategies.pptx|honored|ingestion/strategies/_pptx_parts.py||strategies|",
    "ingestion.strategies.transcript|honored|research/transcript_parser.py|||",
    "ingestion.strategies.web|honored|web/__init__.py|||",
    "installer.data_dir|honored|_setup/install_state.py|State directory||",
    "installer.postgres_image|honored|_setup/setup_common.py||postgres_image|",
    "installer.verify_image_signature|honored|_setup/setup_stack.py|Cosign verify gate||",
    "installer.profiles.minimal.llm installer.profiles.minimal.model "
    "installer.profiles.minimal.ram_gb_max installer.profiles.minimal.vram_gb_max "
    "installer.profiles.balanced.llm installer.profiles.balanced.model "
    "installer.profiles.balanced.ram_gb_max installer.profiles.balanced.vram_gb_max "
    "installer.profiles.performance.llm installer.profiles.performance.model "
    "installer.profiles.performance.ram_gb_max installer.profiles.performance.vram_gb_max|honored|"
    "_setup/profiles.py||ram_gb_max|",
    "judge.enabled|honored|tools/ask_tools.py|Judge gate on ask||",
    "judge.base_url judge.max_claims judge.model|honored|rag/judge.py||max_claims|",
    "notebooks.enabled notebooks.directory|honored|notebooks/settings.py|||",
    "observability.enabled observability.otlp_endpoint observability.service_name|honored|"
    "utils/tracing.py||otlp_endpoint|",
    "queue.autorun|honored|queue/autorun.py|||",
    "queue.max_attempts|honored|tools/queue_tools.py|Retry cap||",
    "queue.poll_seconds|honored|cli/cli_queue.py|Watch poll interval||",
    "queue.stale_lease_seconds|honored|queue/store.py|Lease expiry|stale_lease_seconds|",
    "reranking.allow_remote_urls reranking.backend reranking.base_url reranking.calibration "
    "reranking.enabled reranking.llamacpp_url reranking.model reranking.score_floor "
    "reranking.timeout_seconds reranking.max_pair_tokens|honored|rag/rerank_settings.py|Loader; "
    "pgml refuses on duckdb|max_pair_tokens|",
    "reranking.candidates retrieval.expand_to_parent|honored|handlers/query_stages.py|||",
    "reranking.over_retrieve_n|honored|handlers/query_vector_rows.py|||",
    "reranking.return_top_k|honored|rag/rerank_rules.py|Top-k clamp rule||",
    "research.alt_test.fdr_q research.alt_test.margin research.alt_test.method "
    "research.alt_test.n_bootstrap research.alt_test.passing_fraction research.audit.sample_rate "
    "research.audit.seed research.labeling.aggregation research.labeling.cv_folds "
    "research.labeling.flag_threshold research.labeling.min_signals "
    "research.prompt_stability.alpha_threshold research.prompt_stability.min_variants|honored|"
    "coding/measurement_settings.py|Alt-test/audit/labeling/stability|fdr_q|",
    "research.consensus.min_run_fraction research.consensus.runs "
    "research.consensus.similarity_threshold|honored|research/cycle_consensus.py|||",
    "research.cycle.halt_on|honored|research/cycle_gates.py|Halt conditions||",
    "research.embedder.dimensions|honored|_setup/doctor_research.py|||",
    "research.inductive.centroid_drift_threshold research.inductive.entropy_threshold "
    "research.inductive.recluster_every_batches research.inductive.tau_dup|honored|"
    "research/inductive_run.py||tau_dup|",
    "research.ingest.watch_interval_s|honored|cli/cli_ingest.py|||",
    "research.judgment_distribution.use_mean|honored|coding/judgment_sampling.py|||",
    "research.release.default_profile|honored|research/release/profiles.py||default_profile|",
    "research.release.enforcement|honored|research/release/enforcement_settings.py|||",
    "research.review.batch_size research.review.random_fraction|honored|research/review_batch.py||"
    "random_fraction|",
    "research.review.diversity research.routing.confidence_level "
    "research.routing.min_calibration_n research.routing.target_risk|honored|"
    "research/review_routing.py||diversity|",
    "retrieval.mode retrieval.vector_candidates retrieval.lexical_candidates retrieval.rrf_k "
    "retrieval.exclude_degraded|honored|research/retrieval_settings.py||vector_candidates|",
    "retrieval.matryoshka_enabled retrieval.matryoshka_dim retrieval.candidate_multiplier|"
    "honored-both-query-arm-pg-optimization-only|handlers/query_search_pg.py|R8: PG query-arm "
    "optimization|matryoshka|",
    "retrieval.exact_filter_selectivity_threshold|postgres-query-arm-plus-research-both|"
    "handlers/query_search_pg.py|PG query-arm scan; research both|exact_filter|",
    "retrieval.hnsw.m retrieval.hnsw.ef_construction retrieval.hnsw.iterative_scan "
    "retrieval.hnsw.ef_search retrieval.hnsw.max_scan_tuples|refused-under-duckdb-when-set|"
    "backend_gates.py|G16-G20: pgvector-only knobs|retrieval.hnsw.|HNSW_GATE_KEYS",
    "retrieval.multi_query.enabled retrieval.multi_query.variants "
    "retrieval.multi_query.timeout_seconds retrieval.multi_query.model "
    "retrieval.multi_query.base_url|honored|research/multi_query_settings.py||base_url|",
    "routing.prototypes.graph routing.prototypes.relational routing.prototypes.semantic|honored|"
    "handlers/router_handler.py||prototypes|",
    "server.port|honored|server_port.py|HTTP bind port||",
    "server.openai_compat|honored|api/http.py|||",
    "server.socket_path server.socket_port|honored|api/socket_target.py|||",
    "statements.enabled statements.templates_dir|honored|tools/pipeline/atomize.py|Atomizer gate "
    "and templates|templates_dir|",
    "statements.max_per_chunk|honored|statements/atomize.py|Per-chunk statement cap||",
    "telemetry.enabled telemetry.log_dir|honored|telemetry/settings.py|||",
    "textbook.min_pages textbook.window_pages textbook.parser textbook.cache_dir "
    "textbook.window_subprocess textbook.window_timeout_s textbook.cache_max_mb "
    "textbook.worker_reuse textbook.worker_max_windows textbook.worker_idle_ttl_s "
    "textbook.structured_output textbook.citations|honored|"
    "ingestion/strategies/textbook_settings.py|Windowing, cache, workers|window_subprocess|",
    "topics.enabled topics.method topics.min_topic_size topics.llm topics.llm_base_url "
    "topics.llm_reduction topics.fixture_dir topics.live_fallback topics.zeroshot_min_similarity|"
    "honored|topics/settings.py||min_topic_size|",
    "ui.spa.max_upload_mb ui.spa.poll_ms ui.spa.request_timeout_s|honored|ui/spa.py||"
    "max_upload_mb|",
    "web.user_agent web.timeout_seconds web.max_bytes web.respect_robots web.max_pages "
    "web.max_depth web.delay_seconds web.allow_private_addresses|honored|web/settings.py||"
    "respect_robots|",
    "research.release.profiles.exploratory.alpha_reliable "
    "research.release.profiles.exploratory.alpha_tentative "
    "research.release.profiles.exploratory.min_audit_sample "
    "research.release.profiles.exploratory.min_stratum_size "
    "research.release.profiles.exploratory.partition_rule "
    "research.release.profiles.exploratory.required_gates "
    "research.release.profiles.high-assurance.agreement_routing_threshold "
    "research.release.profiles.high-assurance.alpha_reliable "
    "research.release.profiles.high-assurance.alpha_tentative "
    "research.release.profiles.high-assurance.alt_test_margin "
    "research.release.profiles.high-assurance.alt_test_passing_fraction "
    "research.release.profiles.high-assurance.content_stability_floor "
    "research.release.profiles.high-assurance.max_stratum_disparity "
    "research.release.profiles.high-assurance.min_audit_sample "
    "research.release.profiles.high-assurance.min_stratum_size "
    "research.release.profiles.high-assurance.partition_rule "
    "research.release.profiles.high-assurance.per_code_f1_floor "
    "research.release.profiles.high-assurance.prompt_stability_alpha "
    "research.release.profiles.high-assurance.required_gates "
    "research.release.profiles.team-codebook.agreement_routing_threshold "
    "research.release.profiles.team-codebook.alpha_reliable "
    "research.release.profiles.team-codebook.alpha_tentative "
    "research.release.profiles.team-codebook.alt_test_margin "
    "research.release.profiles.team-codebook.alt_test_passing_fraction "
    "research.release.profiles.team-codebook.content_stability_floor "
    "research.release.profiles.team-codebook.max_stratum_disparity "
    "research.release.profiles.team-codebook.min_audit_sample "
    "research.release.profiles.team-codebook.min_stratum_size "
    "research.release.profiles.team-codebook.partition_rule "
    "research.release.profiles.team-codebook.per_code_f1_floor "
    "research.release.profiles.team-codebook.prompt_stability_alpha "
    "research.release.profiles.team-codebook.required_gates|honored|research/release/profiles.py||"
    "alpha_reliable|",
    "master.idle_window_s master.switch_window_s|honored|master/state_store.py|Windows||",
    "master.workspace_root|honored|tools/pipeline/stages.py|Ingest attribution root||",
    "master.adopt_on_start master.adopt_skip master.backup.enabled master.backup.schedule "
    "master.backup.time_local master.backup.target_dir master.scan_interval_s|honored|"
    "_setup/doctor_master.py|Doctor reports; W7 and W8 read later|adopt_skip|",
]


def _parse_rows() -> tuple[ConfigBackendRow, ...]:
    """Expand the ``_RAW`` pipe records into one row per leaf key."""
    rows: list[ConfigBackendRow] = []
    for record in _RAW:
        keys, behavior, reader, note, token, bind = record.split("|")
        if behavior == "duckdb-only":
            backends = DUCKDB
        elif behavior.startswith("refused"):
            backends = POSTGRES
        else:
            backends = BOTH
        for key in keys.split():
            rows.append(ConfigBackendRow(key, backends, behavior, reader, note, token, bind))
    return tuple(rows)


def matrix_rows() -> tuple[ConfigBackendRow, ...]:
    """Every config-leaf declaration row, in declaration order."""
    return _parse_rows()


def declared_leaf_keys() -> tuple[str, ...]:
    """Every declared dotted leaf key (the known-key inventory)."""
    return tuple(row.key for row in matrix_rows())

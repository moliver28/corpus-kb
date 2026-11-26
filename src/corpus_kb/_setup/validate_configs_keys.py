"""Runtime ``config.yaml`` key map and rule constants.

Dotted key -> expected YAML type for every key the shipped configs must
carry (plan §1: "validate_configs.py learns every new key"), plus the
enum sets, range bounds, and backend-mix error strings the cross-field
rules use. Data only — the rules live in ``validate_configs_rules.py``
and ``validate_configs_runtime.py``.
"""

from __future__ import annotations

from pathlib import Path

# The rerank rules are shared with rag/rerank_settings.py (ONE rules
# module, rag/rerank_rules.py). Same two-context shape as the extraction
# module: the CI validate-configs job installs only PyYAML, so the
# fallback loads the file directly (stdlib-only imports, no rag package init).
try:
    # Package context (installed checkout, tests).
    from corpus_kb.rag.rerank_rules import (
        SUPPORTED_RERANK_BACKENDS,
        backend_error,
        return_top_k_error,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib.util

    _rules_path = Path(__file__).resolve().parents[1] / "rag" / "rerank_rules.py"
    _spec = importlib.util.spec_from_file_location("rerank_rules", _rules_path)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"Cannot load rerank rules from {_rules_path}") from None
    _rules = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_rules)
    SUPPORTED_RERANK_BACKENDS = _rules.SUPPORTED_RERANK_BACKENDS
    backend_error = _rules.backend_error
    return_top_k_error = _rules.return_top_k_error

# dotted key -> expected YAML type (bool/str/int/float/list/dict)
RUNTIME_CONFIG_KEYS: dict[str, type] = {
    # extraction.backend is the preferred key; graph.extractor is the
    # accepted legacy alias (see corpus_kb.extraction.extraction_backend_name).
    "extraction.backend": str,
    "server.socket_path": str,
    "server.socket_port": int,
    "embedding.mrl_trained": bool,
    "embedding.late_chunking": bool,
    "embedding.query_instruction": bool,
    "ingestion.default": str,
    "ingestion.strategies": dict,
    "ingestion.concurrency": int,
    # ingestion.tokenizer / dedup / dedup_threshold are NOT here: absent is
    # the shipped default; when-present checks live in validate_ingestion_block.
    # -- ingest.chunking (canonical home of the hierarchy knobs) -----------
    "ingest.chunking.enabled": bool,
    "ingest.chunking.child_tokens": int,
    "ingest.chunking.chunk_tokens": int,
    "ingest.chunking.parent_tokens": int,
    "ingest.chunking.overlap_tokens": int,
    "ingest.chunking.respect_boundaries": list,
    # -- ingest.contextualize (renamed from contextual.*) ------------------
    "ingest.contextualize.enabled": bool,
    # W3 contextualize v2 keys (rag/contextual_settings.py owns the rules).
    "ingest.contextualize.backend": str,
    "ingest.contextualize.model": str,
    "ingest.contextualize.prompt_profile": str,
    "ingest.contextualize.max_context_tokens": int,
    "ingest.contextualize.document_first_prompt": bool,
    "ingest.contextualize.apply_to": list,
    # -- retrieval (renamed from search.*) ----------------------------------
    "retrieval.mode": str,
    "retrieval.vector_candidates": int,
    "retrieval.lexical_candidates": int,
    "retrieval.rrf_k": int,
    "retrieval.expand_to_parent": bool,
    "retrieval.hnsw.m": int,
    "retrieval.hnsw.ef_construction": int,
    # -- reranking (renamed from search.rerank.*) ---------------------------
    "reranking.enabled": bool,
    "reranking.return_top_k": int,
    # reranking.backend: null is the shipped default; union rule in _reranking_block_errors.
    "reranking.llamacpp_url": str,
    # -- website (URL) ingestion (src/corpus_kb/web/, WebFetchConfig) -------
    "web.user_agent": str,
    "web.timeout_seconds": int,
    "web.max_bytes": int,
    "web.respect_robots": bool,
    "web.max_pages": int,
    "web.max_depth": int,
    "web.delay_seconds": float,
    "web.allow_private_addresses": bool,
    # -- topics (Wave 4, src/corpus_kb/topics/) ------------------------------
    # min_topic_size is str|int ("auto" or a positive int): the union is
    # enforced by _topics_block_errors below. The zero-shot floor range
    # rule (0, 1] lives in the topics loader.
    "topics.enabled": bool,
    "topics.method": str,
    "topics.llm_reduction": bool,
    "topics.llm": str,
    "topics.llm_base_url": str,
    "topics.zeroshot_min_similarity": float,
    # -- server (HTTP adapter gates) -----------------------------------------
    "server.openai_compat": bool,
    # -- database (storage-portability program §3) ----------------------------
    "database.backend": str,
    # -- graph traversal ------------------------------------------------------
    "graph.max_hops": int,
    # DuckDB graph traversal engine (textbook W1; Postgres ignores the key).
    "graph.duckdb_graph_backend": str,
    # -- textbook ingestion (textbook-ingestion program, Wave 1) -------------
    "textbook.min_pages": int,
    "textbook.window_pages": int,
    # textbook.parser enum: SUPPORTED_TEXTBOOK_PARSERS (cross-field rules).
    "textbook.parser": str,
    "textbook.cache_dir": str,
    # Window isolation + cache cap (issues #219/#221): window_timeout_s is
    # a positive number, cache_max_mb allows 0; rules in _textbook_block_errors.
    "textbook.window_subprocess": bool,
    "textbook.window_timeout_s": float,
    "textbook.cache_max_mb": int,
    # Persistent parser worker (issue #270): worker_reuse keeps one loaded
    # parser child; worker_max_windows recycles it; worker_idle_ttl_s drops
    # an idle worker (rules in _textbook_block_errors).
    "textbook.worker_reuse": bool,
    "textbook.worker_max_windows": int,
    "textbook.worker_idle_ttl_s": float,
    # Rich-structure flag (textbook W1-B): keeps level, page boxes, and
    # table markdown in the parsed windows.
    "textbook.structured_output": bool,
    # Citation flag (textbook W2-B): writes a page + section citation
    # string into each chunk's metadata.
    "textbook.citations": bool,
    # -- ingest queue worker knobs (tools/queue_tools.py, Wave 1) ------------
    "queue.autorun": bool,
    "queue.poll_seconds": int,
    "queue.stale_lease_seconds": int,
    "queue.max_attempts": int,
    # -- graph_policy (Wave 1; enum in the cross-field rules) -----------------
    "graph_policy.mode": str,
    # -- ui (UI SPA program Task 3, src/corpus_kb/ui/spa.py) ----------------
    "ui.spa.poll_ms": int,
    "ui.spa.request_timeout_s": float,
    "ui.spa.max_upload_mb": int,
    # -- notebooks (UX program Wave 6, src/corpus_kb/notebooks/) -------------
    "notebooks.enabled": bool,
    "notebooks.directory": str,
    # -- observability (UX program Wave 4, src/corpus_kb/utils/tracing.py) ---
    "observability.enabled": bool,
    "observability.otlp_endpoint": str,
    "observability.service_name": str,
    # -- telemetry (issue #442, src/corpus_kb/telemetry/) --------------------
    "telemetry.enabled": bool,
    "telemetry.log_dir": str,
}

SUPPORTED_RETRIEVAL_MODES = frozenset({"hybrid_rrf"})
# database.backend values (storage-portability program §3).
SUPPORTED_DATABASE_BACKENDS = frozenset({"postgres", "duckdb"})
# graph.max_hops: traversal depth cap. The ceiling is today's AGE cap.
MAX_HOPS_MIN = 1
MAX_HOPS_CEILING = 25
# Backend-mix refusal texts (program §11 rule 15 bank, VERBATIM). This
# module cannot import corpus_kb (the CI validate-configs job installs only
# PyYAML); a lockstep test pins equality with corpus_kb.backend_gates.
AGE_ON_DUCKDB_ERROR = "Set database.backend to postgres, or remove the graph.backend setting."
PGML_EMBEDDER_ON_DUCKDB_ERROR = (
    "PostgresML runs only on Postgres. The Ollama embedder will be used instead."
)
PGML_RERANKER_ON_DUCKDB_ERROR = (
    "PostgresML reranking runs only on Postgres. The Ollama reranker will be used instead."
)
PGML_EXTRACTION_ON_DUCKDB_ERROR = (
    "PostgresML extraction runs only on Postgres. The regex extractor will be used instead."
)
HNSW_ON_DUCKDB_ERROR = (
    "retrieval.hnsw.* is pgvector-only. Remove the block, or set database.backend to postgres."
)
DUCKDB_GRAPH_BACKEND_ON_POSTGRES_ERROR = (
    "graph.duckdb_graph_backend is a DuckDB setting. Remove it, or set database.backend to duckdb."
)
GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR = (
    "graph.backend: postgres cannot run on the DuckDB engine."
    " Remove graph.backend, or set database.backend to postgres."
)
# The three texts above are mirrored for the lockstep pin only: the
# explicit-set rules fire at load in corpus_kb.backend_gates.
SUPPORTED_CONTEXTUAL_PROFILES = frozenset({"interview", "generic"})
SUPPORTED_APPLY_TO_TARGETS = frozenset({"embedding", "lexical"})
SUPPORTED_TOPIC_METHODS = frozenset({"bertopic"})
# reranking.backend and reranking.return_top_k rules are shared with
# rag/rerank_settings.py — see the rerank_rules import above.
# textbook.parser values (textbook-ingestion Wave 1). "docling" is the
# structured page parser ([docling] extra); "paddle-vl" the PaddleOCR-VL
# challenger ([ocr-vl] extra); "pypdf" the scan-only fallback ([scan] extra).
SUPPORTED_TEXTBOOK_PARSERS = frozenset({"docling", "paddle-vl", "pypdf"})
# graph_policy.mode values (Wave 1): "all" mirrors today's write behavior;
# "semantic_only" admits only graph_eligible relation types.
SUPPORTED_GRAPH_POLICY_MODES = frozenset({"all", "semantic_only"})
# graph.duckdb_graph_backend values (DuckDB graph traversal engine).
SUPPORTED_DUCKDB_GRAPH_BACKENDS = frozenset({"cte", "duckpgq"})

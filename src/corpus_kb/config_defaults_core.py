"""Core config defaults (W5-1 split): the first half of the default tree.

The literal moved verbatim out of ``config.py`` so that file shrinks and
each defaults module stays at or under the 250-line limit. The assembled
``DEFAULT_CONFIG`` lives in ``config_defaults.py``; the config-lockstep
test pins the packaged yaml to these leaves.
"""

from __future__ import annotations

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS, RESEARCH_EMBEDDING_DIMENSIONS

_CORE_CONFIG: dict[str, object] = {
    "server": {
        "host": "localhost",
        "port": 8010,
        # Gates the OpenAI-compatible /v1 adapter (api/routes_v1.py);
        # on by default so chat frontends work out of the box.
        "openai_compat": True,
        # JSON-RPC socket pair (issue #348: one path per study server).
        # Full derive rules live in the packaged config.yaml comments.
        "socket_path": "",
        "socket_port": 0,
    },
    "graph": {
        "backend": "age",
        # DuckDB graph traversal engine (graph.backend: duckdb only):
        # "cte" (recursive-CTE walker, deterministic) or "duckpgq" (the
        # DuckPGQ extension). Postgres ignores the key.
        "duckdb_graph_backend": "cte",
        # Graph traversal depth cap (1..25; 25 is today's AGE cap).
        # Consumed by the neighbor/traverse operations (program §5.2).
        "max_hops": 5,
        # Accepted legacy alias for extraction.backend (kept forever);
        # see apply_extraction_backend_alias.
        "extractor": "ml",
        "model_version": "langextract-default",
        "prompt_version": "v1",
        # Recorded-fixture extraction (LangExtract members): the fixture
        # corpus and the live-call escape hatch. fixture_dir is the
        # repo-relative tests path; installed runs resolve it from CWD
        # exactly as before — the packaged yaml carries the same value.
        "fixture_dir": "tests/fixtures/langextract_recorded",
        "live_fallback": False,
        "ontology_path": "config/ontology.yaml",
    },
    "embedding": {
        # ollama serves the 4B embedder; pgml cannot host a model of
        # this size.
        "provider": "ollama",
        "fallback_provider": "ollama",
        "model": "qwen3-embedding:4b",
        "base_url": "http://localhost:11434",
        "batch_size": 32,
        # Issue #468: hard cap on the per-call ``input`` array.
        "max_batch_texts": 32,
        # Native output width. Must equal the schema width
        # (chunks_vectors.embedding = halfvec(EMBEDDING_DIMENSIONS)).
        "dimensions": EMBEDDING_DIMENSIONS,
        # U32 MRL gating: qwen3-embedding is Matryoshka-trained, so
        # front-slice truncation + L2 renormalization is sound. Gates
        # the matryoshka arm and the Ollama dimensions passthrough.
        "mrl_trained": True,
        # Gates QUERY_INSTRUCTION_PREFIX at instruct() (query side only).
        "query_instruction": True,
        # Late chunking gate; honest pooled fallback. The packaged
        # config.yaml holds the full contract.
        "late_chunking": True,
    },
    "ingestion": {
        # Bounded parallel file ingestion (Wave 1 throughput); the
        # runtime helper caps it at two below the pool size.
        "concurrency": 4,
        # Pluggable strategy mapping (source_type -> strategy name) and
        # the fallback strategy; see corpus_kb.ingestion.registry.
        "strategies": {
            "transcript": "transcript",
            "pptx": "pptx_structure",
            "web": "web_structure",
            "survey": "survey",
        },
        "default": "default",
    },
    # Textbook ingestion (textbook-ingestion program). Full parses
    # need the [docling] extra; these are the gate + window + parser
    # declarations the validate_configs gate pins (types and enums
    # in RUNTIME_CONFIG_KEYS). window_subprocess isolates each
    # Docling window in a child process (issue #219); cache_max_mb
    # evicts the oldest window-cache files past the cap (issue #221).
    # structured_output keeps rich page structure in the windows.
    # citations writes a plain page + section citation into each
    # chunk's metadata. worker_reuse keeps one loaded parser worker
    # child alive across windows (issue #270); worker_max_windows
    # recycles that worker after the given window count;
    # worker_idle_ttl_s drops a worker idle longer than that at the
    # next acquire, so an idle server sheds its parser children.
    "textbook": {
        "min_pages": 120,
        "window_pages": 50,
        "parser": "docling",
        "cache_dir": "~/.corpus-kb/textbook-cache",
        "window_subprocess": True,
        "window_timeout_s": 3600.0,
        "worker_reuse": True,
        "worker_max_windows": 32,
        "worker_idle_ttl_s": 600.0,
        "cache_max_mb": 2048,
        "structured_output": False,
        "citations": False,
    },
    # Ingest queue worker knobs (tools/queue_tools.py + the Wave 2
    # worker). autorun: false keeps the worker off; enqueue-only is
    # the shipped default.
    "queue": {
        "autorun": False,
        "poll_seconds": 20,
        "stale_lease_seconds": 600,
        "max_attempts": 3,
    },
    # Graph write policy: "all" mirrors today's behavior (every
    # extracted relation reaches the graph store); "semantic_only"
    # admits only the ontology's graph_eligible relation types.
    "graph_policy": {
        "mode": "all",
    },
    "retrieval": {
        # Only hybrid_rrf exists today; other values are rejected at
        # load time (load_retrieval_settings).
        "mode": "hybrid_rrf",
        # Live per-arm candidate depths before fusion (effective limit
        # is max(candidates, k * 2), so small values never under-fetch
        # below k).
        "vector_candidates": 150,
        "lexical_candidates": 150,
        "rrf_k": 60,
        # Post-fusion small-to-big parent expansion (moved from
        # ingest.hierarchy; fail-open).
        "expand_to_parent": True,
        # Stage 3 degraded policy: candidate CTEs drop rows flagged
        # degraded (migration 022).
        "exclude_degraded": True,
        # U21: below this estimated selectivity, filtered queries take
        # the exact scan instead of the HNSW probe.
        "exact_filter_selectivity_threshold": 0.05,
        # Matryoshka two-tier retrieval: candidate fan-out is
        # k * this multiplier.
        "candidate_multiplier": 8,
        "self_query": {
            # Gates SelfQueryParser construction (server_wiring twins);
            # stays opt-in: on, one LLM call parses predicates per
            # search before fusion.
            "enabled": False,
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
            "timeout_seconds": 8,
        },
        # Issue #136 multi-query rewriting (opt-in; full contract in
        # the packaged config.yaml comments).
        "multi_query": {
            "enabled": False,
            "variants": 3,
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
            "timeout_seconds": 8,
        },
        "matryoshka_enabled": False,
        "matryoshka_dim": RESEARCH_EMBEDDING_DIMENSIONS,
    },
    "reranking": {
        "enabled": True,
        # backend selects the scorer: null = qwen3 judgment scorer via
        # Ollama, "llamacpp" = true cross-encoder client (POST /v1/rerank
        # on llamacpp_url), "generative" = the null alias, "fake" = the
        # test double (build_reranker).
        "backend": None,
        # Committed upgrade-program default: qwen3-reranker 4B. That
        # tag is not yet pullable from the Ollama registry — until a
        # model with it exists locally, the rerank stage degrades to
        # the un-reranked RRF order with a logged warning
        # (rag/rerank_settings.py contract).
        "model": "qwen3-reranker:4b",
        "base_url": "http://localhost:11434",
        # llama.cpp /v1/rerank server for backend: "llamacpp"
        # (llama-server listens on 8080 by default).
        "llamacpp_url": "http://localhost:8080",
        # Remote rerank endpoints are opt-in (SSRF hardening):
        # localhost and private hosts are always allowed.
        "allow_remote_urls": False,
        # Rerank pre-fetch width for score-based rerankers.
        "over_retrieve_n": 60,
        "candidates": 100,
        # Accepted and range-validated now; becomes the default return
        # k (catalogue contract change) in wave 3.
        "return_top_k": 20,
        "max_pair_tokens": 768,
        "timeout_seconds": 120,
        "score_floor": 0.15,
        "calibration": "minmax",
    },
    # U42 provenance-keyed exact result cache, read by
    # corpus_kb.research.exact_cache.load_cache_settings. `enabled` gates
    # every access (the --no-cache flag drops it at the CLI layer);
    # deductive runs default on, inductive discovery runs default off
    # unless seeded.
    "cache": {
        "enabled": True,
        "deductive_enabled": True,
        "inductive_enabled": False,
    },
    "judge": {
        "enabled": True,
        "model": "qwen3:4b",
        "base_url": "http://localhost:11434",
        "max_claims": 20,
    },
    "routing": {
        "enabled": True,
        "confidence_threshold": 0.35,
        "prototypes": {
            "semantic": [
                "what does the document say about X",
                "explain the concept of X",
                "summarize the section on X",
            ],
            "relational": [
                "how many documents mention X",
                "count the chunks by type",
                "aggregate statistics across the corpus",
            ],
            "graph": [
                "what is related to X",
                "how is X connected to Y",
                "what entities are linked to X",
            ],
        },
    },
}

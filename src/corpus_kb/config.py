"""Configuration loader — loads YAML config with environment variable overrides.

Priority (config discovery):
1. Explicit `path` argument
2. CORPUS_KB_CONFIG env var (absolute path) — set by MCP for config discovery
3. ./config.yaml (current working directory, dev mode)
4. Packaged default via importlib.resources (installed mode)

This allows corpus-kb to be called from any directory and still find its config,
which is critical for MCP integration where the working directory is unpredictable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from corpus_kb.config_aliases import apply_legacy_aliases


def _deep_update(base: dict[str, object], overlay: dict[str, object]) -> None:
    """Recursively overlay values onto a base dictionary (mutating base)."""
    for key, value in overlay.items():
        base_value = base.get(key)
        if isinstance(value, dict) and isinstance(base_value, dict):
            _deep_update(
                cast(dict[str, object], base_value),
                cast(dict[str, object], value),
            )
        else:
            base[key] = value


# The config tree is schemaless YAML; Any is the honest leaf type. It lets
# callers index nested sections without casting every hop.
def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config from a YAML file, merged with defaults and env var overrides.

    Args:
        path: Optional explicit config file path

    Returns:
        Configuration dictionary with defaults and env var overrides applied

    Config discovery order:
        1. Explicit path argument
        2. CORPUS_KB_CONFIG environment variable
        3. ./config.yaml in the current working directory
        4. Packaged default config.yaml shipped with corpus_kb
    """
    config = get_default_config()

    config_path: Path | None = None
    if path:
        config_path = Path(path)
    else:
        env_path = os.environ.get("CORPUS_KB_CONFIG")
        if env_path:
            config_path = Path(env_path)

    if config_path is not None and config_path.exists():
        with config_path.open() as f:
            file_config = cast(dict[str, object], yaml.safe_load(f) or {})
        _deep_update(config, file_config)
    elif (Path.cwd() / "config.yaml").exists():
        cwd_config = Path.cwd() / "config.yaml"
        with cwd_config.open() as f:
            file_config = cast(dict[str, object], yaml.safe_load(f) or {})
        _deep_update(config, file_config)
    else:
        import importlib.resources as resources

        ref = resources.files("corpus_kb") / "config.yaml"
        with resources.as_file(ref) as packaged_path:
            with Path(packaged_path).open() as f:
                file_config = cast(dict[str, object], yaml.safe_load(f) or {})
            _deep_update(config, file_config)

    # Legacy namespace aliases (search.* / ingest.hierarchy.* / contextual.*):
    # copied into the canonical keys when the canonical key is absent, with a
    # deprecation warning. Runs BEFORE the typed readers and cross-field gates
    # so every consumer only ever sees canonical keys.
    apply_legacy_aliases(config)

    # Environment variable overrides. Every mapped variable is live: the
    # config key it writes has a real consumer (install.py, install_state.py,
    # server_wiring.py). Removed keys and their variables are gone for good —
    # doctor reports any set-but-unmapped CORPUS_KB_* variable (see
    # KNOWN_CORPUS_KB_ENV_VARS and _setup/doctor_env.py).
    env_overrides: dict[tuple[str, str], str] = {
        ("embedding", "model"): "CORPUS_KB_EMBEDDING_MODEL",
        ("embedding", "dimensions"): "CORPUS_KB_EMBEDDING_DIMENSIONS",
        ("graph", "backend"): "CORPUS_KB_GRAPH_BACKEND",
        ("database", "backend"): "CORPUS_KB_DATABASE_BACKEND",
        ("server", "transport"): "CORPUS_KB_TRANSPORT",
        ("server", "port"): "CORPUS_KB_PORT",
        ("server", "openai_compat"): "CORPUS_KB_OPENAI_COMPAT",
        ("database", "connection_string"): "CORPUS_KB_DATABASE_URL",
        ("observability", "otlp_endpoint"): "CORPUS_KB_OTLP_ENDPOINT",
        ("installer", "data_dir"): "CORPUS_KB_INSTALL_DATA_DIR",
        ("installer", "postgres_image"): "CORPUS_KB_INSTALL_POSTGRES_IMAGE",
        ("installer", "verify_image_signature"): "CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE",
    }

    bool_keys = {"verify_image_signature", "openai_compat"}
    int_keys = {"dimensions", "port"}

    for (section, key), env_var in env_overrides.items():
        value = os.environ.get(env_var)
        if value is not None:
            section_dict = cast(dict[str, object], config[section])
            if key in int_keys:
                section_dict[key] = int(value)
            elif key in bool_keys:
                section_dict[key] = value.lower() in {"1", "true", "yes", "on"}
            else:
                section_dict[key] = value

    apply_extraction_backend_alias(config)

    # Storage-portability backend truth: Postgres-only engine selections are
    # refused while database.backend is duckdb (the mix gate runs AFTER the
    # alias layers, so it sees the effective extraction backend).
    mix_errors = backend_mix_errors(config)
    if mix_errors:
        raise ValueError(mix_errors[0])

    mrl_error = matryoshka_mrl_error(config)
    if mrl_error is not None:
        raise ValueError(mrl_error)

    # W3 contextualize v2: backend/profile/apply_to/max_context_tokens are
    # rejected AT LOAD with the remediation text (rag/contextual_settings.py
    # is the single source of both the rules and the messages).
    from corpus_kb.rag.contextual_settings import contextualize_settings_error

    contextual_error = contextualize_settings_error(config)
    if contextual_error is not None:
        raise ValueError(contextual_error)

    # Wave 4 topics: method/min_topic_size are rejected AT LOAD with the
    # remediation text (topics/settings.py owns the rules and messages).
    from corpus_kb.topics.settings import topics_settings_error

    topics_error = topics_settings_error(config)
    if topics_error is not None:
        raise ValueError(topics_error)

    # Corpus analysis program: labeling.min_signals, classification.tier
    # and topics.over_time.bins/undated are rejected AT LOAD with the
    # remediation text (analysis/settings.py owns the rules and messages).
    from corpus_kb.analysis.settings import analysis_settings_error

    analysis_error = analysis_settings_error(config)
    if analysis_error is not None:
        raise ValueError(analysis_error)

    # UX Wave 3 browser UI: ui.enabled must be a real bool AT LOAD
    # (ui/settings.py owns the rule and the remediation text).
    from corpus_kb.ui.settings import ui_settings_error

    ui_error = ui_settings_error(config)
    if ui_error is not None:
        raise ValueError(ui_error)

    # UX Wave 6 notebooks console: notebooks.enabled must be a real bool and
    # notebooks.directory a non-empty string AT LOAD (notebooks/settings.py
    # owns the rules and the remediation text).
    from corpus_kb.notebooks.settings import notebooks_settings_error

    notebooks_error = notebooks_settings_error(config)
    if notebooks_error is not None:
        raise ValueError(notebooks_error)

    return config


def apply_extraction_backend_alias(config: dict[str, object]) -> None:
    """Sync ``graph.extractor`` with the preferred ``extraction.backend`` key.

    The extraction backend used to live at ``graph.extractor`` — the wrong
    section (embedding uses ``embedding.provider``, the graph store uses
    ``graph.backend``). ``extraction.backend`` is the preferred key now;
    ``graph.extractor`` keeps working forever as the accepted alias.

    Precedence: a non-empty ``extraction.backend`` string WINS and is
    mirrored into ``graph.extractor``, so every consumer that still reads
    the alias (ingest_extract's fallback chain) sees the effective value.
    When only ``graph.extractor`` is set, it stays authoritative — old
    configs and hand-built dicts never silently lose their backend.
    Mutates ``config`` in place; safe on any dict shape.
    """
    extraction = config.get("extraction")
    backend: object | None = None
    if isinstance(extraction, dict):
        raw = extraction.get("backend")
        if isinstance(raw, str) and raw:
            backend = raw
    if backend is None:
        return
    graph = config.get("graph")
    if not isinstance(graph, dict):
        graph = {}
        config["graph"] = graph
    graph["extractor"] = backend


# Every CORPUS_KB_* variable the code reads: the load_config override map
# above plus the two direct readers (config discovery in this module,
# snapshot interval in domain/application.py). doctor reports any set
# variable outside this set as a warning (see _setup/doctor_env.py).
KNOWN_CORPUS_KB_ENV_VARS: frozenset[str] = frozenset(
    {
        "CORPUS_KB_CONFIG",
        "CORPUS_KB_DATABASE_URL",
        "CORPUS_KB_DATABASE_BACKEND",
        "CORPUS_KB_EMBEDDING_MODEL",
        "CORPUS_KB_EMBEDDING_DIMENSIONS",
        "CORPUS_KB_GRAPH_BACKEND",
        "CORPUS_KB_TRANSPORT",
        "CORPUS_KB_PORT",
        "CORPUS_KB_OPENAI_COMPAT",
        "CORPUS_KB_OTLP_ENDPOINT",
        "CORPUS_KB_INSTALL_DATA_DIR",
        "CORPUS_KB_INSTALL_POSTGRES_IMAGE",
        "CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE",
        "CORPUS_KB_SNAPSHOT_PERIOD",
        "CORPUS_KB_TEST_DB",
    }
)


def matryoshka_mrl_error(config: dict[str, object]) -> str | None:
    """U32 cross-field gate on the two-stage matryoshka search arm.

    ``retrieval.matryoshka_enabled: true`` front-slices stored embeddings to
    ``matryoshka_dim`` and re-ranks at full dimension. That is only sound
    when the embedder is Matryoshka-trained (``embedding.mrl_trained:
    true``); truncating a non-MRL model (nomic-embed-text) silently
    degrades retrieval. Returns the refusal message for the invalid
    combination, or None when the arm is off or the model declared
    MRL-trained. ``load_config`` raises the message as a config error, so
    the arm cannot activate silently; type checking of the two keys is
    owned by ``validate_configs.validate_runtime_config``. The legacy
    ``search.matryoshka_enabled`` spelling is normalized by the alias
    layer before this gate runs.
    """
    retrieval = config.get("retrieval")
    embedding = config.get("embedding")
    if not isinstance(retrieval, dict) or not isinstance(embedding, dict):
        return None
    # Truthiness, matching the runtime consumers (QueryHandler /
    # ingest_common read the key through bool()): a quoted "true" or 1 in a
    # hand-edited YAML must trip the gate, not slip past `is not True`.
    if not retrieval.get("matryoshka_enabled"):
        return None
    if embedding.get("mrl_trained") is True:
        return None
    return (
        "retrieval.matryoshka_enabled: true requires embedding.mrl_trained: true — "
        "two-stage matryoshka retrieval front-slices embeddings, which is only "
        "sound for Matryoshka-trained models (qwen3-embedding is MRL-trained; "
        "nomic-embed-text is NOT). Switch the embedding model first, or set "
        "retrieval.matryoshka_enabled: false."
    )


# Backend-mix refusals (storage-portability program §11 rule 15 string
# bank — the texts are binding and used verbatim; validate_configs mirrors
# them because the CI validate-configs job installs only PyYAML and cannot
# import this package. tests/test_backend_config_truth.py pins the
# equality between the two renderings).
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


def _section_dict(config: dict[str, object], section: str) -> dict[str, object]:
    """The named config section when it is a mapping, else an empty dict."""
    node = config.get(section)
    return node if isinstance(node, dict) else {}


def _effective_extraction_backend(config: dict[str, object]) -> str | None:
    """The extraction backend the alias layer would settle on.

    Mirrors ``apply_extraction_backend_alias`` precedence: a non-empty
    ``extraction.backend`` wins; only when it is absent does the legacy
    ``graph.extractor`` alias stay authoritative.
    """
    extraction = _section_dict(config, "extraction")
    raw = extraction.get("backend")
    if isinstance(raw, str) and raw:
        return raw
    graph = _section_dict(config, "graph")
    raw = graph.get("extractor")
    if isinstance(raw, str) and raw:
        return raw
    return None


def backend_mix_errors(config: dict[str, object]) -> list[str]:
    """Refuse Postgres-only engine selections while ``database.backend`` is duckdb.

    One operation, one truth: AGE and PostgresML (embedder, legacy
    ``search.reranker``, extraction) physically cannot run on DuckDB, so a
    duckdb selection that still names one is refused AT LOAD with a
    plain-English remediation — never a silent downgrade. Returns every
    violation in a fixed order (age, embedder, reranker, extraction);
    empty when the backend is not ``duckdb`` (Postgres accepts all of
    these) — an unknown ``database.backend`` value is NOT a mix and is
    left to ``validate_configs``' enum rule. Pure: no IO, no globals.
    """
    database = _section_dict(config, "database")
    if database.get("backend") != "duckdb":
        return []
    errors: list[str] = []
    graph = _section_dict(config, "graph")
    if graph.get("backend") == "age":
        errors.append(AGE_ON_DUCKDB_ERROR)
    embedding = _section_dict(config, "embedding")
    if "pgml" in (embedding.get("provider"), embedding.get("fallback_provider")):
        errors.append(PGML_EMBEDDER_ON_DUCKDB_ERROR)
    search = _section_dict(config, "search")
    if search.get("reranker") == "pgml":
        errors.append(PGML_RERANKER_ON_DUCKDB_ERROR)
    if _effective_extraction_backend(config) == "pgml":
        errors.append(PGML_EXTRACTION_ON_DUCKDB_ERROR)
    return errors


def get_default_config() -> dict[str, object]:
    """Return the default configuration dict (no file needed)."""
    config: dict[str, object] = {
        "server": {
            "transport": "stdio",
            "host": "localhost",
            "port": 8010,
            # Gates the OpenAI-compatible /v1 adapter (api/routes_v1.py);
            # on by default so chat frontends work out of the box.
            "openai_compat": True,
        },
        "graph": {
            "backend": "age",
            # Graph traversal depth cap (1..25; 25 is today's AGE cap).
            # Consumed by the neighbor/traverse operations (program §5.2).
            "max_hops": 5,
            # Accepted legacy alias for extraction.backend (kept forever);
            # see apply_extraction_backend_alias.
            "extractor": "pgml",
            "model_version": "langextract-default",
            "prompt_version": "v1",
        },
        "embedding": {
            # ollama serves the 4B embedder; pgml cannot host a model of
            # this size.
            "provider": "ollama",
            "fallback_provider": "ollama",
            "model": "qwen3-embedding:4b",
            "base_url": "http://localhost:11434",
            "batch_size": 32,
            # Native output width. Must equal the schema width
            # (chunks_vectors.embedding = halfvec(2560)).
            "dimensions": 2560,
            # U32 MRL gating: qwen3-embedding is Matryoshka-trained, so
            # front-slice truncation + L2 renormalization is a sound
            # reduced-dimension representation. Gates the two-stage
            # matryoshka arm (retrieval.matryoshka_enabled) and the Ollama
            # dimensions passthrough.
            "mrl_trained": True,
            # Gates QUERY_INSTRUCTION_PREFIX at instruct() (query side only).
            "query_instruction": True,
            # Late chunking gate (see the packaged config.yaml for the full
            # contract; honest pooled fallback when the extra/model lacks
            # token-level support).
            "late_chunking": True,
        },
        "storage": {
            # chunks_vectors.embedding column type. halfvec is REQUIRED at
            # 2560 dims: pgvector HNSW caps plain vector at 2000, halfvec
            # at 4000 (migration 025).
            "vector_type": "halfvec",
        },
        "retrieval": {
            # Only hybrid_rrf exists today; other values are rejected at
            # load time (load_retrieval_settings).
            "mode": "hybrid_rrf",
            # DuckDB vector-search mode: exact_scan (default, deterministic)
            # or hnsw (the experimental vss extension; doctor warns).
            # Postgres keeps its pgvector HNSW and ignores this key.
            "vector_index": "exact_scan",
            # DuckDB fts-index rebuild debounce (seconds, positive). DuckDB
            # FTS has no auto-refresh; the gateway rebuilds on a debounce.
            "fts_rebuild_debounce_s": 2.0,
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
            "qa_context_in_fts": False,
            # U21: below this estimated selectivity, filtered queries take
            # the exact scan instead of the HNSW probe.
            "exact_filter_selectivity_threshold": 0.05,
            # Matryoshka two-tier retrieval: candidate fan-out is
            # k * this multiplier.
            "candidate_multiplier": 8,
            "self_query": {
                "enabled": True,
                "model": "qwen3:4b",
                "base_url": "http://localhost:11434",
                "timeout_seconds": 8,
            },
            "matryoshka_enabled": False,
            "matryoshka_dim": 1024,
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
        "llm": {
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
        },
        "coding": {
            "coder": "qwen3:8b",
            "model": "qwen3:8b",
            # U43 validate/repair: retries before a response is marked
            # invalid_output; strict enforcement opt-in (default off).
            "repair_retries": 1,
            "require_structured_enforcement": False,
        },
        # research: MUST stay in lockstep with the packaged config.yaml block —
        # write_config's passthrough writes THIS dict into user configs, so a
        # missing block here would silently drop research.* keys (todo-19 (c)).
        "research": {
            "embedder": {
                # model_revision ("1024") and the G1-winning strategy
                # (naive-prefix) are FIXED IN CODE, deliberately not knobs:
                # embedding rows always record revision "1024" and the
                # strategy is a committed experiment result, not a runtime
                # choice. Only the promotion guard is configurable.
                "dimensions": 1024,
            },
            "ingest": {
                "watch_interval_s": 10,
            },
            "inductive": {
                "entropy_threshold": 0.85,
                "recluster_every_batches": 8,
                "centroid_drift_threshold": 0.15,
                "tau_dup": 0.85,
            },
            "cycle": {
                # codebook_promotion is HARD-FLOORED (never removable);
                # this list may only ADD halt gates.
                "halt_on": [
                    "interpretive_code_review",
                    "gray_zone_escalation",
                    "drift_alarm",
                    "overlap_conflict",
                    "threshold_unreliable",
                    "human_parity_breach",
                    "unreleased_draft",
                ],
            },
        },
        "database": {
            # Storage engine selector: postgres (default) or duckdb. The
            # backend-mix gate (backend_mix_errors) refuses Postgres-only
            # engine selections (AGE, pgml) while duckdb is set. Consumed
            # by the storage gateway (storage-portability program §3).
            "backend": "postgres",
            "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5433/corpus_kb",
        },
        # extraction.backend is the PREFERRED extraction-backend key;
        # graph.extractor (above) stays as the accepted legacy alias.
        "extraction": {
            "backend": "pgml",
        },
        # OpenTelemetry tracing (UX program Wave 4). Off by default: the
        # no-op opentelemetry-api tracer makes every span free. Enable with
        # the [otel] extra; see docs/OBSERVABILITY.md. utils/tracing.py is
        # the typed reader (its DEFAULT_* constants must stay in lockstep).
        "observability": {
            "enabled": False,
            "otlp_endpoint": "http://localhost:4318",
            "service_name": "corpus-kb",
        },
        "installer": {
            "postgres_image": "ghcr.io/moliver28/corpus-kb-postgres:0.1.0-pg17",
            "data_dir": str(Path.home() / ".corpus-kb"),
            "verify_image_signature": True,
            # All three profiles embed at 2560 dims (the schema width);
            # only the LLM varies. A different embedding width needs a
            # schema migration first.
            "profiles": {
                "minimal": {
                    "ram_gb_max": 8,
                    "vram_gb_max": 0,
                    "model": "qwen3-embedding:4b",
                    "llm": "qwen3:0.6b",
                },
                "balanced": {
                    "ram_gb_max": 16,
                    "vram_gb_max": 4,
                    "model": "qwen3-embedding:4b",
                    "llm": "qwen3:4b",
                },
                "performance": {
                    "ram_gb_max": 999,
                    "vram_gb_max": 8,
                    "model": "qwen3-embedding:4b",
                    "llm": "qwen3:14b",
                },
            },
        },
    }
    _apply_typed_module_defaults(config)
    return config


def _apply_typed_module_defaults(config: dict[str, object]) -> None:
    """Merge the typed modules' defaults into the default config.

    research.release.* + the measurement blocks, ingest.chunking.*,
    retrieval.hnsw.* come from the owning modules so the defaults in
    config.py, both config.yaml files and the typed readers cannot drift.
    Imported lazily: config.py is loaded before the package is fully
    importable in some tool paths (validate_configs, installer).
    """
    from corpus_kb.coding.measurement_settings import MEASUREMENT_CONFIG_DEFAULTS
    from corpus_kb.ingest.hierarchy_settings import CHUNKING_CONFIG_DEFAULTS
    from corpus_kb.rag.contextual_settings import CONTEXTUAL_CONFIG_DEFAULTS
    from corpus_kb.research.cycle_consensus import CONSENSUS_CONFIG_DEFAULTS
    from corpus_kb.research.release.profiles import RELEASE_CONFIG_DEFAULTS
    from corpus_kb.research.search_settings import HNSW_CONFIG_DEFAULTS
    from corpus_kb.topics.settings import TOPICS_CONFIG_DEFAULTS

    research = cast(dict[str, object], config.setdefault("research", {}))
    # update() alone would alias the modules' nested default dicts into every
    # loaded config, so mutating one loaded config's research.* block would
    # leak into every later load. Copy one level deep (matches the
    # consensus/hierarchy handling below).
    research.update(_detached_defaults(RELEASE_CONFIG_DEFAULTS))
    research.update(_detached_defaults(MEASUREMENT_CONFIG_DEFAULTS))
    # U48 N-run inductive consensus (research/cycle_consensus.py is the typed
    # reader): runs = N independent inductive passes per cycle, aggregated by
    # the release-layer consensus before themes surface.
    research.setdefault("consensus", dict(CONSENSUS_CONFIG_DEFAULTS))
    # Multi-granularity chunk hierarchy + shadow index
    # (ingest/hierarchy_settings.py): the canonical block is ingest.chunking.
    ingest = cast(dict[str, object], config.setdefault("ingest", {}))
    ingest.setdefault("chunking", _detached_defaults(CHUNKING_CONFIG_DEFAULTS))
    # Contextual retrieval (rag/contextual_settings.py is the typed reader;
    # W3 contextualize v2: backend/profile/cap/ordering/apply_to keys).
    ingest.setdefault("contextualize", _detached_defaults(CONTEXTUAL_CONFIG_DEFAULTS))
    # U20 index-build + scan parameters (research/search_settings.py).
    retrieval = cast(dict[str, object], config.setdefault("retrieval", {}))
    retrieval.setdefault("hnsw", _detached_defaults(HNSW_CONFIG_DEFAULTS))
    # Wave 4 topics layer (topics/settings.py is the typed reader): the
    # opt-in BERTopic block, dormant behind topics.enabled: false.
    config.setdefault("topics", _detached_defaults(TOPICS_CONFIG_DEFAULTS))
    # UX Wave 3 browser UI (ui/settings.py is the typed reader): the block
    # MUST exist here or the setup write-back would silently drop ui.*
    # keys from user configs (the same passthrough rule as research above).
    from corpus_kb.ui.settings import UI_CONFIG_DEFAULTS

    config.setdefault("ui", _detached_defaults(UI_CONFIG_DEFAULTS))
    # UX Wave 6 notebooks console (notebooks/settings.py is the typed
    # reader): the marimo console block, mounted at /notebooks behind the
    # [notebook] extra. Same passthrough rule as ui above.
    from corpus_kb.notebooks.settings import NOTEBOOKS_CONFIG_DEFAULTS

    config.setdefault("notebooks", _detached_defaults(NOTEBOOKS_CONFIG_DEFAULTS))
    # Corpus analysis program (analysis/settings.py is the typed reader):
    # the labeling/classification/topics-over-time blocks, every feature
    # dormant behind enabled: false.
    from corpus_kb.analysis.settings import ANALYSIS_CONFIG_DEFAULTS

    config.setdefault("analysis", _detached_defaults(ANALYSIS_CONFIG_DEFAULTS))


def _detached_defaults(defaults: Mapping[str, object]) -> dict[str, object]:
    """Copy defaults deeply enough that no dict/list inside a loaded config
    aliases the owning modules' constants: load_config deep-merges user YAML
    into this result (``_deep_update``), so any shared nested object would
    absorb user overrides process-wide (release.profiles nests three levels).
    """
    return {key: _detached_value(value) for key, value in defaults.items()}


def _detached_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _detached_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detached_value(item) for item in value]
    return value

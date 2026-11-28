"""Feature config defaults (W5-1 split): the rest of the default tree, verbatim from config.py."""

from __future__ import annotations

from corpus_kb.coding.cloud_client import CLOUD_CONFIG_DEFAULTS
from corpus_kb.coding.model_tiers import MODEL_TIERS_DEFAULTS
from corpus_kb.coding.translation_quality import QUALITY_CONFIG_DEFAULTS
from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

# ui.spa.* defaults (the lifecycle SPA at /app; ui/spa.py is the mount).
# poll_ms: interval between status polls on the Ingest and Scale pages.
SPA_POLL_MS_DEFAULT = 2000
# request_timeout_s: how long one SPA API call waits before giving up.
SPA_REQUEST_TIMEOUT_S_DEFAULT = 60
# max_upload_mb: the per-file upload cap (taxonomy 413 above it).
SPA_MAX_UPLOAD_MB_DEFAULT = 50

_FEATURE_CONFIG: dict[str, object] = {
    "llm": {
        "model": "qwen3:4b",
        "base_url": "http://localhost:11434",
    },
    # TAO T1 (TD-04): bands coding.tier resolves through (reader coding/model_tiers.py).
    "models": {
        "tiers": MODEL_TIERS_DEFAULTS,
    },
    "coding": {
        # "" = unset (AM-10): the shipped defaults never shadow coding.tier;
        # the coder/model pair resolves through models.tiers (qwen3:8b on
        # the default medium tier). User-set values still override.
        "coder": "",
        "model": "",
        # TD-06: start rung; resolved through models.tiers.
        "tier": "medium",
        # TD-06/D-7: 0 sends no judgment-sampling seed; above 0 binds it.
        "judgment_seed": 0,
        # TD-05: cloud rungs behind consent (refine D-6).
        "cloud": CLOUD_CONFIG_DEFAULTS,
        # TD-12: quality floor + site-provided LID weights.
        "quality": QUALITY_CONFIG_DEFAULTS,
        # TAO T0 (TD-16): directory for the codebook CSV tables. Empty
        # means coding_export writes no files.
        "export_dir": "",
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
            # strategy is a committed experiment result. Only the
            # promotion guard is configurable.
            "dimensions": RESEARCH_EMBEDDING_DIMENSIONS,
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
    # "ml" is the shipped default (the packaged yaml value wins at
    # runtime either way; aligning the code side keeps duckdb
    # code-fallback environments loadable — pgml is Postgres-only and
    # the backend-mix gate refuses it on duckdb AT LOAD).
    "extraction": {
        "backend": "ml",
        # zero_shot: GLiNER2 NER + GLiNER-Relex relations under one
        # extractor; few_shot/customized route to LangExtract instead.
        "mode": "zero_shot",
        # Opt-in GLiNER2 NER knobs (the standalone "gliner" backend
        # refuses to construct while enabled is false).
        "gliner": {
            "enabled": False,
            "model": "fastino/gliner2-large-v1",
            "revision": "main",
            "types": ["person", "organization", "location", "date", "money", "product"],
        },
        # GLiNER-Relex zero-shot relation member.
        "relex": {
            "model": "knowledgator/gliner-relex-large-v1.0",
            "threshold": 0.5,
            "max_label_pairs": 40,
        },
        # LoRA adapter tuning (extraction-tune method=lora). The adapter
        # is a candidate artifact; the eval promotion gate decides.
        "tune": {
            "model": "knowledgator/gliner-multitask-large-v0.5",
            "steps": 200,
            "learning_rate": 5e-05,
            "batch_size": 2,
            "lora_r": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.05,
        },
        # Optional voting across extractors (issue #133): members run
        # beside the primary; candidates survive on majority agreement.
        "ensemble": {
            "enabled": False,
            "members": ["langextract"],
        },
        # Chunk-hash result cache (migration 030): re-ingesting
        # unchanged content skips the extraction pass.
        "cache": {
            "enabled": True,
        },
    },
    # Statement atoms (Living Corpus W2, migration 031): chunks
    # atomize into classified, bi-temporal statement rows.
    "statements": {
        "enabled": True,
        "templates_dir": "config/claim_templates",
        # Per-chunk cap on atoms; overflow is dropped and reported.
        "max_per_chunk": 12,
    },
    # Website (URL) ingestion; ingest_url builds WebFetchConfig from
    # this section (web/settings.py). Values mirror the WebFetchConfig
    # dataclass defaults.
    "web": {
        "user_agent": "corpus-kb/0.1 (+https://github.com/moliver28/corpus-kb)",
        "timeout_seconds": 30,
        "max_bytes": 5242880,
        "respect_robots": True,
        "max_pages": 5,
        "max_depth": 2,
        "delay_seconds": 1.0,
        # SSRF-guard escape hatch; true only for genuinely local targets.
        "allow_private_addresses": False,
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
        # Literal tilde form: ~ expands at READ time (install_state
        # expanduser), never at import time — an import-time
        # Path.home() baked the authoring machine's path into the
        # defaults and broke machine-independent comparison.
        "data_dir": "~/.corpus-kb",
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
    # Lifecycle SPA (UI SPA program Task 3; ui/spa.py mounts it at
    # /app). The spa block is runtime-tunable and travels over the
    # wire in /app/runtime-config.json. VERIFY_FULL_GROUNDEDNESS is
    # a module constant only: it is the codegen source for Task 4's
    # thresholds and NEVER enters the config tree or the wire.
    "ui": {
        "spa": {
            "poll_ms": SPA_POLL_MS_DEFAULT,
            "request_timeout_s": SPA_REQUEST_TIMEOUT_S_DEFAULT,
            "max_upload_mb": SPA_MAX_UPLOAD_MB_DEFAULT,
        },
    },
    # Opt-in local event log (issue #442): OFF by default. See ADMIN.md.
    "telemetry": {
        "enabled": False,
        "log_dir": "telemetry_logs",
    },
    # Master topology (spec s3): one pool, workspace attribution, and
    # the one-active-project rule. W8 (P3) reads the adopt and backup keys.
    "master": {
        # Root folder whose <slug>/ subfolders name projects; "" is off.
        "workspace_root": "",
        # Adopt every plugin-era per-study database on first start.
        "adopt_on_start": True,
        # Named databases the adoption step leaves alone.
        "adopt_skip": [],
        # Folder mtime sampling cadence for the active-state rule (s).
        "scan_interval_s": 900,
        # A challenger project takes over within this activity window.
        "switch_window_s": 1800,
        # The incumbent keeps the role while its activity is this fresh.
        "idle_window_s": 7200,
        # The one retained backup (W7 owns the scheduler).
        "backup": {
            "enabled": True,
            "schedule": "daily",
            "time_local": "03:00",
            "target_dir": "backups",
        },
    },
}

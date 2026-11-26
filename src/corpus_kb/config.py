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

import copy
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

# Re-exports (graph_factory and tests import these from corpus_kb.config).
# The ``x as x`` spelling marks each name as an intentional re-export, so
# one string bank can serve both import surfaces (ruff isort splits them).
from corpus_kb.backend_gates import (
    AGE_ON_DUCKDB_ERROR as AGE_ON_DUCKDB_ERROR,
)
from corpus_kb.backend_gates import (
    GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR as GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR,
)
from corpus_kb.backend_gates import (
    PGML_EMBEDDER_ON_DUCKDB_ERROR as PGML_EMBEDDER_ON_DUCKDB_ERROR,
)
from corpus_kb.backend_gates import (
    PGML_EXTRACTION_ON_DUCKDB_ERROR as PGML_EXTRACTION_ON_DUCKDB_ERROR,
)
from corpus_kb.backend_gates import (
    PGML_RERANKER_ON_DUCKDB_ERROR as PGML_RERANKER_ON_DUCKDB_ERROR,
)
from corpus_kb.backend_gates import (
    backend_key_errors,
)
from corpus_kb.config_aliases import apply_legacy_aliases
from corpus_kb.config_defaults import DEFAULT_CONFIG
from corpus_kb.config_defaults_features import (
    SPA_MAX_UPLOAD_MB_DEFAULT,
    SPA_POLL_MS_DEFAULT,
    SPA_REQUEST_TIMEOUT_S_DEFAULT,
)
from corpus_kb.errors import ConfigError


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


# The verify badge's "verified" bar (UI SPA program Task 3): at or above this
# groundedness the judge entailed every claim it checked; below it the answer
# is "partial". Hoisted from ui/view_models.py BEFORE that file's deletion so
# the policy keeps one source. Task 4 codegens thresholds.generated.ts from it.
VERIFY_FULL_GROUNDEDNESS = 0.99

# ui.spa.* defaults (the lifecycle SPA at /app; ui/spa.py is the mount)
# moved to config_defaults_features.py with the defaults tree (W5-1 split);
# they are imported above and re-exported for the SPA module and tools.


def ui_spa_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid ``ui.spa`` values, or None when valid.

    Only VALUES are rejected — a partial block fills from the defaults.
    Called from ``load_config`` so a bad value fails AT LOAD, never
    mid-startup. Messages are plain English (STE).
    """
    ui = _section_dict(config, "ui")
    spa = ui.get("spa")
    if spa is None:
        return None
    if not isinstance(spa, dict):
        return f"ui.spa: must be a settings block; got {spa!r}"
    poll = spa.get("poll_ms", SPA_POLL_MS_DEFAULT)
    if not isinstance(poll, int) or isinstance(poll, bool) or not 100 <= poll <= 60000:
        return (
            "ui.spa.poll_ms: must be a whole number from 100 to 60000 "
            "(milliseconds between status polls)."
        )
    timeout = spa.get("request_timeout_s", SPA_REQUEST_TIMEOUT_S_DEFAULT)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0.1 <= timeout <= 600
    ):
        return (
            "ui.spa.request_timeout_s: must be a number from 0.1 to 600 "
            "(seconds one API call waits)."
        )
    upload = spa.get("max_upload_mb", SPA_MAX_UPLOAD_MB_DEFAULT)
    if not isinstance(upload, int) or isinstance(upload, bool) or not 1 <= upload <= 2048:
        return "ui.spa.max_upload_mb: must be a whole number from 1 to 2048 (megabytes per file)."
    return None


#: One row per overridable config key: (config section, config key) ->
#: variable name. The doctor's known-variable registry below derives from
#: this map, so a new mapping can never miss the known set (the drift that
#: shipped an unregistered CORPUS_KB_HOST once).
_ENV_OVERRIDES: dict[tuple[str, str], str] = {
    ("embedding", "model"): "CORPUS_KB_EMBEDDING_MODEL",
    ("embedding", "dimensions"): "CORPUS_KB_EMBEDDING_DIMENSIONS",
    ("embedding", "base_url"): "CORPUS_KB_OLLAMA_HOST",
    ("graph", "backend"): "CORPUS_KB_GRAPH_BACKEND",
    ("database", "backend"): "CORPUS_KB_DATABASE_BACKEND",
    ("server", "host"): "CORPUS_KB_HOST",
    ("server", "port"): "CORPUS_KB_PORT",
    ("server", "socket_path"): "CORPUS_KB_SOCKET_PATH",
    ("server", "openai_compat"): "CORPUS_KB_OPENAI_COMPAT",
    ("database", "connection_string"): "CORPUS_KB_DATABASE_URL",
    ("observability", "otlp_endpoint"): "CORPUS_KB_OTLP_ENDPOINT",
    ("llm", "timeout_s"): "CORPUS_KB_LLM_TIMEOUT_S",
    ("installer", "data_dir"): "CORPUS_KB_INSTALL_DATA_DIR",
    ("installer", "postgres_image"): "CORPUS_KB_INSTALL_POSTGRES_IMAGE",
    ("installer", "verify_image_signature"): "CORPUS_KB_INSTALL_VERIFY_IMAGE_SIGNATURE",
    ("telemetry", "enabled"): "CORPUS_KB_TELEMETRY_ENABLED",
}


def apply_env_overrides(config: dict[str, Any]) -> None:
    """Apply CORPUS_KB_* environment variable overrides to ``config``.

    Every mapped variable is live: the config key it writes has a real
    consumer (install.py, install_state.py, server_wiring.py). Doctor reports
    any set-but-unmapped CORPUS_KB_* variable (see KNOWN_CORPUS_KB_ENV_VARS
    and _setup/doctor_env.py).
    """
    bool_keys = {"verify_image_signature", "openai_compat", "enabled", "adopt_on_start"}
    int_keys = {"dimensions", "port"}
    float_keys = {"timeout_s"}

    for (section, key), env_var in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value is not None:
            section_dict = cast(dict[str, object], config[section])
            if key in int_keys:
                section_dict[key] = int(value)
            elif key in float_keys:
                section_dict[key] = float(value)
            elif key in bool_keys:
                section_dict[key] = value.lower() in {"1", "true", "yes", "on"}
            else:
                section_dict[key] = value


# The config tree is schemaless YAML; Any is the honest leaf type. It lets
# callers index nested sections without casting every hop.
def config_file_path() -> Path | None:
    """The user's config.yaml file, or None when only the packaged default applies.

    Discovery mirrors ``load_config``: ``CORPUS_KB_CONFIG`` first, then
    ``./config.yaml`` in the current working directory. The packaged
    default shipped inside the package is NOT a user file, so it is never
    returned: callers that WRITE config must treat None as a refusal.
    Overlay paths (``CORPUS_KB_CONFIG_OVERLAY``) are reported separately
    by :func:`config_overlay_paths`.
    """
    env_path = os.environ.get("CORPUS_KB_CONFIG")
    if env_path:
        candidate = Path(env_path)
        if candidate.exists():
            return candidate
    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return cwd_config
    return None


def config_overlay_paths(environ: dict[str, str] | None = None) -> list[Path]:
    """The overlay YAML paths from ``CORPUS_KB_CONFIG_OVERLAY``, in order.

    The variable holds one or more paths split on ``os.pathsep`` (on
    Windows ``;``, elsewhere ``:``), so one variable can name several
    study overlays. Empty entries are dropped. The paths are returned as
    configured — existence is checked at load time, where a missing file
    is an error (never a silent skip). Pure: no IO.
    """
    env = os.environ if environ is None else environ
    raw = env.get("CORPUS_KB_CONFIG_OVERLAY", "")
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


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

    Overlays (issue #348): every path in ``CORPUS_KB_CONFIG_OVERLAY``
    (``os.pathsep``-split, merged in order) deep-merges AFTER the main
    file and BEFORE the env overrides, so one study overlay can change a
    few keys without a full config copy. A missing overlay file is an
    error, never a silent skip.
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

    # Issue #348 overlays: deep-merge IN ORDER after the main file, before
    # the alias and env layers. `file_config` stays the MAIN file's tree —
    # _follow_graph_backend's "did the operator choose" question is about
    # the main file's graph backend, not the overlays.
    for overlay_path in config_overlay_paths():
        if not overlay_path.is_file():
            raise ConfigError(
                f"Config overlay file not found: {overlay_path}. "
                "Fix the path in CORPUS_KB_CONFIG_OVERLAY."
            )
        with overlay_path.open() as f:
            overlay_config = cast(dict[str, object], yaml.safe_load(f) or {})
        if not isinstance(overlay_config, dict):
            raise ConfigError(
                f"Config overlay {overlay_path} must hold a YAML mapping of settings."
            )
        _deep_update(config, overlay_config)

    # Legacy namespace aliases (search.* / ingest.hierarchy.* / contextual.*):
    # copied into the canonical keys when absent, with a deprecation warning.
    # Runs BEFORE the typed readers and cross-field gates.
    apply_legacy_aliases(config)

    apply_env_overrides(config)

    apply_extraction_backend_alias(config)

    # Issue #330: an UNCHOSEN graph.backend follows the database backend.
    _follow_graph_backend(config, file_config)

    # Backend-mismatch truth: Postgres-only engine selections are refused
    # while database.backend is duckdb, DuckDB-only settings while it is
    # postgres (the gate sees the aliases and the raw file tree for the
    # explicit-set rules).
    mix_errors = backend_mix_errors(config, file_config)
    if mix_errors:
        raise ValueError(mix_errors[0])

    # Typed module gates AT LOAD, in order (see _typed_gate_errors).
    typed_error = _typed_gate_errors(config)
    if typed_error is not None:
        raise ValueError(typed_error)

    return config


def _typed_gate_errors(config: dict[str, object]) -> str | None:
    """The AT LOAD typed-settings refusals, in load order (first error wins).

    Each gate module owns its rules and remediation text: config_gates
    (Matryoshka guard), contextual/topics/analysis/notebooks/telemetry
    settings, and the ui.spa reader here. Imports stay lazy (tool paths).
    """
    from corpus_kb.analysis.settings import analysis_settings_error
    from corpus_kb.ask.ask_settings import ask_settings_error
    from corpus_kb.config_gates import matryoshka_mrl_error
    from corpus_kb.notebooks.settings import notebooks_settings_error
    from corpus_kb.rag.contextual_settings import contextualize_settings_error
    from corpus_kb.telemetry.settings import telemetry_settings_error
    from corpus_kb.topics.settings import topics_settings_error

    for gate in (
        matryoshka_mrl_error,
        ask_settings_error,
        contextualize_settings_error,
        topics_settings_error,
        analysis_settings_error,
        telemetry_settings_error,
        ui_spa_settings_error,
        notebooks_settings_error,
    ):
        error = gate(config)
        if error is not None:
            return error
    return None


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


# Every CORPUS_KB_* variable the code reads: every value of _ENV_OVERRIDES
# (the load_config override map above) plus the direct readers (config
# discovery in this module, snapshot interval in domain/application.py, the
# golden-bench dir override read by eval/golden_fetch.py, and the browser
# auto-open guard read by server_wiring.browser_open_enabled). doctor
# reports any set variable outside this set as a warning (see
# _setup/doctor_env.py).
KNOWN_CORPUS_KB_ENV_VARS: frozenset[str] = frozenset(_ENV_OVERRIDES.values()) | frozenset(
    {
        # Direct readers: no row in _ENV_OVERRIDES.
        "CORPUS_KB_CONFIG",
        "CORPUS_KB_CONFIG_OVERLAY",
        "CORPUS_KB_SUPERUSER_DSN",
        "CORPUS_KB_SNAPSHOT_PERIOD",
        "CORPUS_KB_TEST_DB",
        "CORPUS_KB_TEXTBOOK_GOLDEN_DIR",
        "CORPUS_KB_UI_OPEN",
    }
)


# The backend-mix refusal constants (AGE_ON_DUCKDB_ERROR, PGML_*_ON_DUCKDB_ERROR)
# are re-exported from corpus_kb.backend_gates (see the import block above) —
# one string bank, one gate body, both import surfaces keep working.


def _section_dict(config: dict[str, object], section: str) -> dict[str, object]:
    """The named config section when it is a mapping, else an empty dict."""
    node = config.get(section)
    return node if isinstance(node, dict) else {}


def _follow_graph_backend(config: dict[str, object], file_config: dict[str, object]) -> None:
    """Point graph.backend at duckdb when the operator never chose one.

    The shipped default 'age' is a Postgres preference, so a duckdb
    database whose config file (or CORPUS_KB_GRAPH_BACKEND) does not name
    a graph store follows duckdb instead of tripping the AGE mix refusal.
    An explicit choice keeps the mix gate (issue #330).
    """
    if str(_section_dict(config, "database").get("backend") or "postgres") != "duckdb":
        return
    graph_cfg = _section_dict(config, "graph")
    chosen = "graph" in file_config and "backend" in _section_dict(file_config, "graph")
    if (
        graph_cfg.get("backend") == "age"
        and not chosen
        and "CORPUS_KB_GRAPH_BACKEND" not in os.environ
    ):
        graph_cfg["backend"] = "duckdb"


def backend_mix_errors(
    config: dict[str, object], file_config: dict[str, object] | None = None
) -> list[str]:
    """Refuse engine-mismatched backend keys, in one fixed order.

    Thin wrapper over :func:`corpus_kb.backend_gates.backend_key_errors`
    (one operation, one truth). ``file_config`` is the RAW user file tree:
    ``load_config`` passes it so the explicit-set rules (retrieval.hnsw.*,
    graph.backend, graph.duckdb_graph_backend) fire AT LOAD; hand-built
    dicts pass nothing, and only the always-on rules (age, pgml under
    duckdb) can fire. Empty when no rule matches — an unknown
    ``database.backend`` is NOT a mix (``validate_configs``' enum rule
    owns it). Pure: no IO, no globals.
    """
    database_backend = str(_section_dict(config, "database").get("backend") or "postgres")
    return backend_key_errors(config, file_config, database_backend)


def get_default_config() -> dict[str, object]:
    """Return the default configuration dict (no file needed)."""
    # The literal lives in config_defaults_* (W5-1 split); the typed
    # module merge stays here so its imports stay lazy (validate_configs,
    # the installer, and other tool paths load config.py first).
    config = copy.deepcopy(DEFAULT_CONFIG)
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
    from corpus_kb.chunking.hierarchy_settings import CHUNKING_CONFIG_DEFAULTS
    from corpus_kb.coding.measurement_settings import MEASUREMENT_CONFIG_DEFAULTS
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
    # (chunking/hierarchy_settings.py): the canonical block is ingest.chunking.
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
    # UI SPA browser UI (ui/spa.py reads ui.spa.* with defaults): the
    # block MUST exist here or the setup write-back would silently drop
    # ui.spa.* keys from user configs (the same passthrough rule as
    # research above).
    config.setdefault(
        "ui",
        _detached_defaults(
            {
                "spa": {
                    "poll_ms": SPA_POLL_MS_DEFAULT,
                    "request_timeout_s": SPA_REQUEST_TIMEOUT_S_DEFAULT,
                    "max_upload_mb": SPA_MAX_UPLOAD_MB_DEFAULT,
                }
            }
        ),
    )
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
    # Opt-in telemetry (issue #442; telemetry/settings.py is the typed reader).
    from corpus_kb.telemetry.settings import TELEMETRY_CONFIG_DEFAULTS

    config.setdefault("telemetry", _detached_defaults(TELEMETRY_CONFIG_DEFAULTS))
    from corpus_kb.ask.ask_settings import ASK_CONFIG_DEFAULTS  # issue #540

    config.setdefault("ask", _detached_defaults(ASK_CONFIG_DEFAULTS))


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

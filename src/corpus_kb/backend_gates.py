"""Backend-mismatch gates — one truth for engine-vs-key conflicts, both directions.

Every backend-mismatch rule lives here, in one fixed order: Postgres-only
selections under the DuckDB engine, and DuckDB-only settings under the
Postgres engine. ``config.load_config`` calls :func:`backend_key_errors`
AT LOAD with the raw user file tree, so a mismatched key fails at startup
with a plain-English remediation — never a silent downgrade.
``storage.graph_factory`` re-checks the same function for hand-built
config dicts, which carry no raw file tree, so there only the always-on
rules (age, pgml) can fire.

The explicit-set rules read the RAW user file config (``file_config``),
never the merged config: the merged config always carries the shipped
defaults (``_detached_defaults`` fills ``retrieval.hnsw.*``), and a
default must never trip a refusal. A key counts as explicitly set when
the raw file carries a value that differs from the shipped default —
the shipped YAMLs and the setup write-back render the defaults into user
config files, so mere presence would refuse every shipped config.
"""

from __future__ import annotations

from collections.abc import Mapping

from corpus_kb.research.search_settings import HNSW_CONFIG_DEFAULTS
from corpus_kb.utils.config_sections import mapping_section

# Backend-mix refusals (storage-portability program §11 rule 15 string
# bank — the texts are binding and used verbatim; validate_configs mirrors
# them because the CI validate-configs job installs only PyYAML and cannot
# import this package. tests/test_backend_config_truth.py pins the
# equality between the two renderings). The three gate-completion texts
# below follow the same bank rules: one line, a named remediation.
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

#: The five pgvector-only HNSW knobs (G16-G20), in DDL order.
HNSW_GATE_KEYS: tuple[str, ...] = (
    "m",
    "ef_construction",
    "iterative_scan",
    "ef_search",
    "max_scan_tuples",
)

#: Sentinel for "the raw file does not carry this key" (a file may carry
#: None as a value, so None cannot double as the missing marker).
_ABSENT = object()


def _file_value(file_config: dict[str, object] | None, dotted: str) -> object:
    """The value at a dotted path in the RAW user file config, or _ABSENT.

    Walks only real mappings; a scalar mid-path ends the walk. No merged
    config, no defaults — the caller decides what counts as a choice.
    """
    parts = dotted.split(".")
    node: object = file_config
    for part in parts[:-1]:
        if not isinstance(node, Mapping):
            return _ABSENT
        node = node.get(part)
    if not isinstance(node, Mapping):
        return _ABSENT
    return node.get(parts[-1], _ABSENT)


def _explicitly_set(file_config: dict[str, object] | None, dotted: str) -> bool:
    """True when the raw user file config carries the dotted key at all.

    None-safe: a ``None`` file tree (programmatic loads, the factory
    re-check) never carries a key, so explicit-set rules cannot fire
    without a user file.
    """
    return _file_value(file_config, dotted) is not _ABSENT


def _tuned_off_default(file_config: dict[str, object] | None, dotted: str, default: object) -> bool:
    """True when the raw file sets the dotted key to a non-default value.

    Shipped YAMLs and setup write-backs render the defaults into user
    files, so presence alone would refuse untouched installs. Only a
    value the operator actually chose counts.
    """
    value = _file_value(file_config, dotted)
    return value is not _ABSENT and value != default


def _effective_extraction_backend(config: dict[str, object]) -> str | None:
    """The extraction backend the alias layer would settle on.

    Mirrors ``config.apply_extraction_backend_alias`` precedence: a
    non-empty ``extraction.backend`` wins; only when it is absent does
    the legacy ``graph.extractor`` alias stay authoritative.
    """
    extraction = mapping_section(config, "extraction")
    raw = extraction.get("backend")
    if isinstance(raw, str) and raw:
        return raw
    graph = mapping_section(config, "graph")
    raw = graph.get("extractor")
    if isinstance(raw, str) and raw:
        return raw
    return None


def backend_key_errors(
    config: dict[str, object],
    file_config: dict[str, object] | None,
    database_backend: str,
) -> list[str]:
    """Every backend-mismatch refusal for ``database_backend``, in fixed order.

    Order: age, pgml embedder, pgml reranker, pgml extraction (always on
    under duckdb); the retrieval.hnsw block (G16-G20); an explicit
    ``graph.backend: postgres`` under duckdb (G09); an explicit non-default
    ``graph.duckdb_graph_backend`` under postgres (the mirror). The
    explicit-set rules compare against the raw ``file_config`` tree — pass
    ``None`` (hand-built dicts, the factory re-check) and only the
    always-on rules can fire. An unknown ``database.backend`` is NOT a
    mix and returns no errors — ``validate_configs``' enum rule owns that
    refusal. Pure: no IO, no globals.
    """
    errors: list[str] = []
    if database_backend == "duckdb":
        graph = mapping_section(config, "graph")
        if graph.get("backend") == "age":
            errors.append(AGE_ON_DUCKDB_ERROR)
        embedding = mapping_section(config, "embedding")
        if "pgml" in (embedding.get("provider"), embedding.get("fallback_provider")):
            errors.append(PGML_EMBEDDER_ON_DUCKDB_ERROR)
        search = mapping_section(config, "search")
        if search.get("reranker") == "pgml":
            errors.append(PGML_RERANKER_ON_DUCKDB_ERROR)
        if _effective_extraction_backend(config) == "pgml":
            errors.append(PGML_EXTRACTION_ON_DUCKDB_ERROR)
        if any(
            _tuned_off_default(file_config, f"retrieval.hnsw.{key}", HNSW_CONFIG_DEFAULTS[key])
            for key in HNSW_GATE_KEYS
        ):
            errors.append(HNSW_ON_DUCKDB_ERROR)
        if _explicitly_set(file_config, "graph.backend") and graph.get("backend") == "postgres":
            # Both checks matter: the file must CHOOSE postgres (the
            # follow-flip writes "duckdb" and must never trip), and the
            # merged value must STILL say postgres (an env override wins).
            errors.append(GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR)
    elif database_backend == "postgres":
        if _tuned_off_default(file_config, "graph.duckdb_graph_backend", "cte"):
            errors.append(DUCKDB_GRAPH_BACKEND_ON_POSTGRES_ERROR)
    return errors

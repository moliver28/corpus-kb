"""Backend-gate tests (parity program PR-6: G09, G16-G20).

Covers ``corpus_kb.backend_gates.backend_key_errors``: the moved
Postgres-only refusals keep their byte-identical texts, the five
``retrieval.hnsw`` keys refuse under duckdb only when the raw user file
tunes them off the shipped defaults, and both mirror directions refuse
explicitly chosen keys. The load_config pass-through test proves the
explicit-set rules fire AT LOAD.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from corpus_kb.backend_gates import (
    DUCKDB_GRAPH_BACKEND_ON_POSTGRES_ERROR,
    GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR,
    HNSW_GATE_KEYS,
    HNSW_ON_DUCKDB_ERROR,
    backend_key_errors,
)
from corpus_kb.config import (
    KNOWN_CORPUS_KB_ENV_VARS,
    backend_mix_errors,
    get_default_config,
    load_config,
)

# String bank literals copied from config.py BEFORE the PR-6 move
# (storage-portability program §11 rule 15): the moved rules must behave
# identically after the move, byte for byte.
AGE_TEXT = "Set database.backend to postgres, or remove the graph.backend setting."
PGML_EMBEDDER_TEXT = "PostgresML runs only on Postgres. The Ollama embedder will be used instead."
PGML_RERANKER_TEXT = (
    "PostgresML reranking runs only on Postgres. The Ollama reranker will be used instead."
)
PGML_EXTRACTION_TEXT = (
    "PostgresML extraction runs only on Postgres. The regex extractor will be used instead."
)

# Non-default values a raw file could tune each HNSW knob to.
HNSW_TUNED_VALUES: dict[str, object] = {
    "m": 8,
    "ef_construction": 64,
    "iterative_scan": "relaxed_order",
    "ef_search": 400,
    "max_scan_tuples": 10000,
}


def _backend_config(backend: str) -> dict[str, object]:
    """The default config with ``database.backend`` set to ``backend``."""
    config = get_default_config()
    database = cast(dict[str, object], config.setdefault("database", {}))
    database["backend"] = backend
    return config


def _duck_config() -> dict[str, object]:
    """A duckdb selection with no always-on misselection left selected.

    The shipped ``graph.backend: age`` default follows the engine when the
    operator never chose one (``_follow_graph_backend``), so a clean duck
    config carries ``graph.backend: duckdb`` at gate time.
    """
    config = _backend_config("duckdb")
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["backend"] = "duckdb"
    return config


def _pgml_config(backend: str) -> dict[str, object]:
    """A config with every always-on duckdb misselection present."""
    config = _duck_config() if backend == "duckdb" else _backend_config(backend)
    embedding = cast(dict[str, object], config.setdefault("embedding", {}))
    embedding["provider"] = "pgml"
    search = cast(dict[str, object], config.setdefault("search", {}))
    search["reranker"] = "pgml"
    extraction = cast(dict[str, object], config.setdefault("extraction", {}))
    extraction["backend"] = "pgml"
    return config


@pytest.fixture
def no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every mapped CORPUS_KB_* env var before each test."""
    for var in KNOWN_CORPUS_KB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_hnsw_block_refused_under_duckdb() -> None:
    """A raw file that tunes one hnsw key gets the block-level refusal."""
    config = _duck_config()
    file_config: dict[str, object] = {"retrieval": {"hnsw": {"ef_search": 400}}}
    errors = backend_key_errors(config, file_config, "duckdb")
    assert HNSW_ON_DUCKDB_ERROR in errors


def test_hnsw_defaults_never_refuse() -> None:
    """No file tree, no hnsw block, or default values: no refusal ever."""
    config = _duck_config()
    assert backend_key_errors(config, None, "duckdb") == []
    assert backend_key_errors(config, {}, "duckdb") == []
    block_only: dict[str, object] = {"retrieval": {}}
    assert backend_key_errors(config, block_only, "duckdb") == []
    shipped_values: dict[str, object] = {
        "retrieval": {"hnsw": {"m": 16, "ef_construction": 128, "ef_search": 200}}
    }
    assert backend_key_errors(config, shipped_values, "duckdb") == []


@pytest.mark.parametrize("key", sorted(HNSW_GATE_KEYS))
def test_each_hnsw_key_triggers(key: str) -> None:
    """Each of the five hnsw keys pins its own G16-G20 refusal (G16-G20)."""
    config = _duck_config()
    file_config: dict[str, object] = {"retrieval": {"hnsw": {key: HNSW_TUNED_VALUES[key]}}}
    errors = backend_key_errors(config, file_config, "duckdb")
    assert errors == [HNSW_ON_DUCKDB_ERROR]


def test_duckdb_graph_backend_refused_under_postgres() -> None:
    """The mirror direction: a duck-only traversal choice under postgres."""
    config = _backend_config("postgres")
    tuned: dict[str, object] = {"graph": {"duckdb_graph_backend": "duckpgq"}}
    errors = backend_key_errors(config, tuned, "postgres")
    assert errors == [DUCKDB_GRAPH_BACKEND_ON_POSTGRES_ERROR]
    # The shipped default value is inert: the shipped YAMLs render it.
    shipped: dict[str, object] = {"graph": {"duckdb_graph_backend": "cte"}}
    assert backend_key_errors(config, shipped, "postgres") == []


def test_graph_backend_postgres_refused_under_duckdb() -> None:
    """G09: an explicit postgres graph backend under the duckdb engine."""
    config = _duck_config()
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["backend"] = "postgres"  # load_config merges the file over defaults
    file_config: dict[str, object] = {"graph": {"backend": "postgres"}}
    errors = backend_key_errors(config, file_config, "duckdb")
    assert errors == [GRAPH_BACKEND_POSTGRES_ON_DUCKDB_ERROR]
    # The default-flip writes "duckdb" — an unchosen backend never trips.
    graph["backend"] = "duckdb"
    flipped: dict[str, object] = {"graph": {"backend": "duckdb"}}
    assert backend_key_errors(config, flipped, "duckdb") == []


def test_age_and_pgml_refusals_preserved() -> None:
    """The moved rules return the pre-move literals, in the fixed order."""
    config = _pgml_config("duckdb")
    # The age rule reads the merged config, like it did before the move.
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["backend"] = "age"
    errors = backend_key_errors(config, None, "duckdb")
    assert errors == [
        AGE_TEXT,
        PGML_EMBEDDER_TEXT,
        PGML_RERANKER_TEXT,
        PGML_EXTRACTION_TEXT,
    ]


def test_unknown_database_backend_not_a_mix() -> None:
    """An unknown engine value is the enum rule's job, never a mix."""
    config = _pgml_config("weird")
    file_config: dict[str, object] = {
        "graph": {"backend": "age", "duckdb_graph_backend": "duckpgq"},
        "retrieval": {"hnsw": {"ef_search": 400}},
    }
    assert backend_key_errors(config, file_config, "weird") == []


def test_wrapper_keeps_backward_signature() -> None:
    """backend_mix_errors(config) still answers with the always-on rules."""
    assert backend_mix_errors(get_default_config()) == []
    assert backend_mix_errors(_pgml_config("duckdb")) == [
        PGML_EMBEDDER_TEXT,
        PGML_RERANKER_TEXT,
        PGML_EXTRACTION_TEXT,
    ]
    # Explicit-set rules stay load-time-only: no file tree, no hnsw refusal.
    tuned = _duck_config()
    retrieval = cast(dict[str, object], tuned.setdefault("retrieval", {}))
    hnsw = cast(dict[str, object], retrieval.setdefault("hnsw", {}))
    hnsw["ef_search"] = 400
    assert backend_mix_errors(tuned) == []


def test_load_config_passes_file_tree_to_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_env_overrides: None
) -> None:
    """load_config feeds the raw file tree in, so hnsw refuses AT LOAD."""
    monkeypatch.chdir(tmp_path)
    raw: dict[str, object] = {
        "database": {"backend": "duckdb"},
        "retrieval": {"hnsw": {"ef_search": 400}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_config()
    assert HNSW_ON_DUCKDB_ERROR in str(excinfo.value)


def test_load_config_shipped_defaults_load_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_env_overrides: None
) -> None:
    """A write-back style file (defaults rendered) loads on both engines."""
    monkeypatch.chdir(tmp_path)
    raw: dict[str, object] = {
        "database": {"backend": "postgres"},
        "graph": {"duckdb_graph_backend": "cte"},
        "retrieval": {"hnsw": {"m": 16, "ef_construction": 128}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config()["database"]["backend"] == "postgres"

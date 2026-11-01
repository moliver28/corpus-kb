"""Database-backend config truth (storage-portability program, phase A).

Covers the four new keys — ``database.backend``, ``graph.max_hops``,
``retrieval.vector_index``, ``retrieval.fts_rebuild_debounce_s`` — the
``CORPUS_KB_DATABASE_BACKEND`` env override, the backend-mix refusal
(pure function in ``config.py`` + ``load_config`` raise +
``validate_configs`` surfacing), and doctor env-var recognition.

Rule-2 five-file lockstep: config.py, BOTH config.yaml files,
RUNTIME_CONFIG_KEYS, docs/ADMIN.md. The semantic-equality test
(test_config_resources.py) guards the YAML pair.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from corpus_kb._setup import validate_configs
from corpus_kb._setup.doctor_env import env_var_check, unknown_env_vars
from corpus_kb._setup.doctor_research import STATUS_OK
from corpus_kb.config import (
    KNOWN_CORPUS_KB_ENV_VARS,
    backend_mix_errors,
    get_default_config,
    load_config,
)

ROOT = Path(__file__).resolve().parents[1]

# String bank (storage-portability program §11 rule 15) — used VERBATIM.
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


@pytest.fixture
def no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every mapped CORPUS_KB_* env var before each test."""
    for var in KNOWN_CORPUS_KB_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Defaults (get_default_config + the shipped YAML pair)
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_backend_is_postgres(self):
        database = cast(dict[str, object], get_default_config()["database"])
        assert database["backend"] == "postgres"

    def test_default_max_hops_is_five(self):
        graph = cast(dict[str, object], get_default_config()["graph"])
        assert graph["max_hops"] == 5

    def test_default_vector_index_is_exact_scan(self):
        retrieval = cast(dict[str, object], get_default_config()["retrieval"])
        assert retrieval["vector_index"] == "exact_scan"

    def test_default_fts_debounce_is_two_seconds(self):
        retrieval = cast(dict[str, object], get_default_config()["retrieval"])
        assert retrieval["fts_rebuild_debounce_s"] == 2.0

    def test_defaults_have_no_backend_mix(self):
        assert backend_mix_errors(get_default_config()) == []

    @pytest.mark.parametrize("name", ["config.yaml", "src/corpus_kb/config.yaml"])
    def test_shipped_yamls_carry_the_new_keys(self, name: str):
        cfg = cast(dict[str, object], yaml.safe_load((ROOT / name).read_text(encoding="utf-8")))
        database = cast(dict[str, object], cfg["database"])
        graph = cast(dict[str, object], cfg["graph"])
        retrieval = cast(dict[str, object], cfg["retrieval"])
        assert database["backend"] == "postgres"
        assert graph["max_hops"] == 5
        assert retrieval["vector_index"] == "exact_scan"
        assert retrieval["fts_rebuild_debounce_s"] == 2.0


# ---------------------------------------------------------------------------
# Env override
# ---------------------------------------------------------------------------


class TestEnvOverride:
    def test_known_env_vars_includes_database_backend(self):
        assert "CORPUS_KB_DATABASE_BACKEND" in KNOWN_CORPUS_KB_ENV_VARS

    def test_env_var_overrides_backend(self, tmp_path: Path):
        payload = {
            "database": {"backend": "duckdb"},
            "graph": {"backend": "postgres"},
            "extraction": {"backend": "regex"},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("CORPUS_KB_DATABASE_BACKEND", "postgres")
            config = load_config(str(path))
        database = cast(dict[str, object], config["database"])
        assert database["backend"] == "postgres"


# ---------------------------------------------------------------------------
# backend_mix_errors — the pure gate
# ---------------------------------------------------------------------------


def _duckdb_config() -> dict[str, object]:
    """A valid DuckDB selection: no Postgres-only engine left selected."""
    config = get_default_config()
    cast(dict[str, object], config["database"])["backend"] = "duckdb"
    cast(dict[str, object], config["graph"])["backend"] = "postgres"
    cast(dict[str, object], config["extraction"])["backend"] = "regex"
    return config


class TestBackendMixErrors:
    def test_postgres_backend_accepts_age_and_pgml(self):
        # The default extraction backend is "ml" (issue #246 owner
        # decision); postgres accepts every engine selection either way.
        assert backend_mix_errors(get_default_config()) == []

    def test_duckdb_with_age_graph_is_refused(self):
        config = _duckdb_config()
        cast(dict[str, object], config["graph"])["backend"] = "age"
        assert backend_mix_errors(config) == [AGE_ON_DUCKDB_ERROR]

    def test_duckdb_with_pgml_embedder_is_refused(self):
        config = _duckdb_config()
        embedding = cast(dict[str, object], config["embedding"])
        embedding["provider"] = "pgml"
        assert backend_mix_errors(config) == [PGML_EMBEDDER_ON_DUCKDB_ERROR]

    def test_duckdb_with_pgml_fallback_embedder_is_refused(self):
        config = _duckdb_config()
        embedding = cast(dict[str, object], config["embedding"])
        embedding["fallback_provider"] = "pgml"
        assert backend_mix_errors(config) == [PGML_EMBEDDER_ON_DUCKDB_ERROR]

    def test_duckdb_with_legacy_pgml_reranker_is_refused(self):
        config = _duckdb_config()
        config["search"] = {"reranker": "pgml"}
        assert backend_mix_errors(config) == [PGML_RERANKER_ON_DUCKDB_ERROR]

    def test_duckdb_with_pgml_extraction_is_refused(self):
        config = _duckdb_config()
        cast(dict[str, object], config["extraction"])["backend"] = "pgml"
        assert backend_mix_errors(config) == [PGML_EXTRACTION_ON_DUCKDB_ERROR]

    def test_duckdb_with_legacy_extractor_alias_alone_is_refused(self):
        config = _duckdb_config()
        del cast(dict[str, object], config["extraction"])["backend"]
        cast(dict[str, object], config["graph"])["extractor"] = "pgml"
        assert backend_mix_errors(config) == [PGML_EXTRACTION_ON_DUCKDB_ERROR]

    def test_extraction_backend_wins_over_stale_extractor_alias(self):
        # The alias layer mirrors extraction.backend into graph.extractor, so
        # a non-empty extraction.backend is authoritative: a stale pgml
        # extractor alias next to a supported backend must NOT fire.
        config = _duckdb_config()
        cast(dict[str, object], config["graph"])["extractor"] = "pgml"
        assert backend_mix_errors(config) == []

    def test_all_mixes_report_together_in_order(self):
        config = _duckdb_config()
        cast(dict[str, object], config["graph"])["backend"] = "age"
        cast(dict[str, object], config["extraction"])["backend"] = "pgml"
        config["search"] = {"reranker": "pgml"}
        cast(dict[str, object], config["embedding"])["provider"] = "pgml"
        assert backend_mix_errors(config) == [
            AGE_ON_DUCKDB_ERROR,
            PGML_EMBEDDER_ON_DUCKDB_ERROR,
            PGML_RERANKER_ON_DUCKDB_ERROR,
            PGML_EXTRACTION_ON_DUCKDB_ERROR,
        ]

    def test_missing_database_section_is_clean(self):
        assert backend_mix_errors({"graph": {"backend": "age"}}) == []

    def test_unknown_backend_value_is_not_a_mix(self):
        # Typo values are the enum gate's job (validate_configs); the mix
        # gate refuses only the declared duckdb value.
        config = get_default_config()
        cast(dict[str, object], config["database"])["backend"] = "DuckDB"
        assert backend_mix_errors(config) == []

    def test_plain_language_sentences(self):
        # Program §11 rule 15: one sentence, <= 20 words, no banned idioms.
        banned = ("just", "simply", "obviously", "utilize", "leverage", "etc")
        for message in (
            AGE_ON_DUCKDB_ERROR,
            PGML_EMBEDDER_ON_DUCKDB_ERROR,
            PGML_RERANKER_ON_DUCKDB_ERROR,
            PGML_EXTRACTION_ON_DUCKDB_ERROR,
        ):
            lowered = message.lower()
            assert not any(word in lowered for word in banned), message
            for sentence in message.split(". "):
                assert len(sentence.split()) <= 20, message


# ---------------------------------------------------------------------------
# load_config raises on the mix
# ---------------------------------------------------------------------------


class TestLoadConfigGate:
    def test_duckdb_default_selection_raises_with_age_remedy(self, tmp_path: Path):
        # Defaults still select the age graph store, so a bare duckdb
        # switch fails fast with the first (age) remediation. The default
        # extraction backend is "ml" (issue #246 owner decision), so only
        # the age mix fires — an explicitly configured pgml backend still
        # trips its own error (test below).
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"database": {"backend": "duckdb"}}), encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            load_config(str(path))
        assert AGE_ON_DUCKDB_ERROR in str(excinfo.value)

    def test_duckdb_clean_selection_loads(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"database": {"backend": "duckdb"}}), encoding="utf-8")
        # The default age/pgml mix still raises; the clean selection loads.
        clean = tmp_path / "clean.yaml"
        payload = {
            "database": {"backend": "duckdb"},
            "graph": {"backend": "postgres"},
            "extraction": {"backend": "regex"},
        }
        clean.write_text(yaml.safe_dump(payload), encoding="utf-8")
        config = load_config(str(clean))
        database = cast(dict[str, object], config["database"])
        assert database["backend"] == "duckdb"

    def test_duckdb_clean_defaults_load_after_backend_default_change(self, tmp_path: Path):
        # Issue #246 owner decision: the default extraction backend is
        # "ml", so a duckdb switch plus a postgres graph store now loads
        # cleanly (the old pgml default tripped the mix gate at load).
        path = tmp_path / "config.yaml"
        payload = {
            "database": {"backend": "duckdb"},
            "graph": {"backend": "postgres"},
        }
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        config = load_config(str(path))
        database = cast(dict[str, object], config["database"])
        assert database["backend"] == "duckdb"

    def test_duckdb_explicit_pgml_extraction_still_raises_after_aliases(self, tmp_path: Path):
        # An EXPLICIT pgml extraction backend stays refused on duckdb; the
        # alias layer runs BEFORE the mix gate. The alias-only spelling is
        # a hand-built-dict concern only: the merged default always fills
        # the preferred key (extraction.backend), which then wins.
        path = tmp_path / "config.yaml"
        payload = {
            "database": {"backend": "duckdb"},
            "graph": {"backend": "postgres"},
            "extraction": {"backend": "pgml"},
        }
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            load_config(str(path))
        assert PGML_EXTRACTION_ON_DUCKDB_ERROR in str(excinfo.value)


# ---------------------------------------------------------------------------
# validate_configs: presence+type+enum for the new keys, mix surfacing
# ---------------------------------------------------------------------------


class TestValidateConfigsKeys:
    def test_new_keys_declared(self):
        keys = validate_configs.RUNTIME_CONFIG_KEYS
        assert keys["database.backend"] is str
        assert keys["graph.max_hops"] is int
        assert keys["retrieval.vector_index"] is str
        assert keys["retrieval.fts_rebuild_debounce_s"] is float
        # Wave 4 opt-in gates stay OPTIONAL: absent is the shipped
        # default, so they are checked when-present (validate_ingestion_block).
        assert "ingestion.tokenizer" not in keys
        assert "ingestion.dedup" not in keys

    @pytest.mark.parametrize("name", ["config.yaml", "src/corpus_kb/config.yaml"])
    def test_shipped_yamls_pass_runtime_validation(self, name: str):
        cfg = validate_configs.load_yaml(ROOT / name)
        assert validate_configs.validate_runtime_config(cfg) == [], name

    def test_missing_backend_key_fails(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        del cast(dict[str, object], config["database"])["backend"]
        errors = validate_configs.validate_runtime_config(config)
        assert any("missing required key 'database.backend'" in e for e in errors)

    def test_backend_enum_locked(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        cast(dict[str, object], config["database"])["backend"] = "sqlite"
        errors = validate_configs.validate_runtime_config(config)
        assert any("database.backend" in e and "duckdb" in e for e in errors)

    def test_max_hops_range_enforced(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        graph = cast(dict[str, object], config["graph"])
        graph["max_hops"] = 0
        assert any("graph.max_hops" in e for e in validate_configs.validate_runtime_config(config))
        graph["max_hops"] = 26
        assert any("graph.max_hops" in e for e in validate_configs.validate_runtime_config(config))
        graph["max_hops"] = 25
        assert validate_configs.validate_runtime_config(config) == []
        graph["max_hops"] = True
        errors = validate_configs.validate_runtime_config(config)
        assert any("key 'graph.max_hops' must be int" in e for e in errors)

    def test_vector_index_enum_locked(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        retrieval = cast(dict[str, object], config["retrieval"])
        retrieval["vector_index"] = "ivfflat"
        errors = validate_configs.validate_runtime_config(config)
        assert any("retrieval.vector_index" in e and "hnsw" in e for e in errors)

    def test_fts_debounce_positive_float(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        retrieval = cast(dict[str, object], config["retrieval"])
        retrieval["fts_rebuild_debounce_s"] = 0
        errors = validate_configs.validate_runtime_config(config)
        assert any("retrieval.fts_rebuild_debounce_s" in e for e in errors)
        retrieval["fts_rebuild_debounce_s"] = -1.0
        errors = validate_configs.validate_runtime_config(config)
        assert any("retrieval.fts_rebuild_debounce_s" in e for e in errors)
        retrieval["fts_rebuild_debounce_s"] = 2  # int accepted for float keys
        assert validate_configs.validate_runtime_config(config) == []
        retrieval["fts_rebuild_debounce_s"] = True
        errors = validate_configs.validate_runtime_config(config)
        assert any("key 'retrieval.fts_rebuild_debounce_s' must be float" in e for e in errors)


class TestValidateConfigsMix:
    def _mixed_config(self) -> dict[str, object]:
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        cast(dict[str, object], config["database"])["backend"] = "duckdb"
        cast(dict[str, object], config["graph"])["backend"] = "age"
        cast(dict[str, object], config["extraction"])["backend"] = "pgml"
        config["search"] = {"reranker": "pgml"}
        cast(dict[str, object], config["embedding"])["provider"] = "pgml"
        return config

    def test_mix_errors_surfaced(self):
        errors = validate_configs.validate_runtime_config(self._mixed_config())
        for expected in (
            AGE_ON_DUCKDB_ERROR,
            PGML_EMBEDDER_ON_DUCKDB_ERROR,
            PGML_RERANKER_ON_DUCKDB_ERROR,
            PGML_EXTRACTION_ON_DUCKDB_ERROR,
        ):
            assert expected in errors, expected

    def test_clean_duckdb_selection_surfaces_nothing(self):
        config = cast(dict[str, object], validate_configs.load_yaml(ROOT / "config.yaml"))
        cast(dict[str, object], config["database"])["backend"] = "duckdb"
        cast(dict[str, object], config["graph"])["backend"] = "postgres"
        cast(dict[str, object], config["extraction"])["backend"] = "regex"
        assert validate_configs.validate_runtime_config(config) == []

    def test_messages_match_config_py_verbatim(self):
        # Lockstep: the standalone validator (CI installs only PyYAML) must
        # surface the exact strings the loader raises — nothing more, and
        # nothing reworded.
        bank = {
            AGE_ON_DUCKDB_ERROR,
            PGML_EMBEDDER_ON_DUCKDB_ERROR,
            PGML_RERANKER_ON_DUCKDB_ERROR,
            PGML_EXTRACTION_ON_DUCKDB_ERROR,
        }
        scenarios: list[dict[str, object]] = [
            self._mixed_config(),
            _duckdb_config(),
            get_default_config(),
        ]
        for scenario in scenarios:
            expected = backend_mix_errors(scenario)
            surfaced = [e for e in validate_configs.validate_runtime_config(scenario) if e in bank]
            assert expected == surfaced, scenario


# ---------------------------------------------------------------------------
# doctor env-var recognition
# ---------------------------------------------------------------------------


class TestDoctorEnvRecognition:
    def test_database_backend_env_var_is_known(self):
        env = {"CORPUS_KB_DATABASE_BACKEND": "duckdb"}
        assert unknown_env_vars(env) == []

    def test_env_var_check_stays_ok(self):
        check = env_var_check({"CORPUS_KB_DATABASE_BACKEND": "duckdb"})
        assert check.status == STATUS_OK

    def test_truly_unknown_vars_still_warn(self):
        env = {"CORPUS_KB_DATABASE_BACKEND": "duckdb", "CORPUS_KB_NOT_A_THING": "1"}
        assert unknown_env_vars(env) == ["CORPUS_KB_NOT_A_THING"]

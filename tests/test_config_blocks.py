from __future__ import annotations


def test_all_new_feature_config_blocks_load() -> None:
    from corpus_kb.config import load_config

    cfg = load_config()
    assert "enabled" in cfg["reranking"]
    assert "return_top_k" in cfg["reranking"]
    assert "self_query" in cfg["retrieval"]
    assert "matryoshka_dim" in cfg["retrieval"]
    assert cfg["ingest"]["contextualize"]["enabled"] is False  # opt-in default per feature table
    assert cfg["judge"]["enabled"] is True  # max-priority always-on feature
    assert cfg["routing"]["enabled"] is True  # max-priority always-on feature


def test_ingestion_strategy_config_block_loads() -> None:
    """S4: ingestion.strategies mapping + ingestion.default, root and packaged in lockstep."""
    from pathlib import Path

    import yaml

    from corpus_kb.config import load_config

    cfg = load_config()
    ingestion = cfg["ingestion"]
    assert ingestion["strategies"] == {
        "transcript": "transcript",
        "pptx": "pptx_structure",
        "web": "web_structure",
    }
    assert ingestion["default"] == "default"

    root = Path(__file__).resolve().parent.parent
    for name in ("config.yaml", "src/corpus_kb/config.yaml"):
        raw = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        assert raw["ingestion"]["strategies"]["transcript"] == "transcript", name
        assert raw["ingestion"]["default"] == "default", name


def test_return_top_k_is_the_catalogue_default_k() -> None:
    """W3 lockstep: reranking.return_top_k IS the default return k.

    The catalogue pins the default k shown to every surface (and the
    harness pack); the shipped YAML and the typed module must carry the
    SAME number, or an omitted-k search would resolve differently from
    the documented default. This test is the drift alarm.
    """
    from pathlib import Path

    import yaml

    from corpus_kb.contracts import CORE_OPERATIONS
    from corpus_kb.rag.rerank_settings import DEFAULT_RETURN_TOP_K

    defaults = {
        op.name: {p.name: p.default for p in op.params if p.name == "k"} for op in CORE_OPERATIONS
    }
    assert defaults["search"] == {"k": DEFAULT_RETURN_TOP_K}
    assert defaults["query"] == {"k": DEFAULT_RETURN_TOP_K}

    root = Path(__file__).resolve().parent.parent
    for name in ("config.yaml", "src/corpus_kb/config.yaml"):
        raw = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        assert raw["reranking"]["return_top_k"] == DEFAULT_RETURN_TOP_K, name

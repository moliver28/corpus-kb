from __future__ import annotations


def test_all_new_feature_config_blocks_load() -> None:
    from corpus_kb.config import load_config

    cfg = load_config()
    assert "rerank" in cfg["search"]
    assert "self_query" in cfg["search"]
    assert "matryoshka_dim" in cfg["search"]
    assert cfg["contextual"]["enabled"] is False  # opt-in default per feature table
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

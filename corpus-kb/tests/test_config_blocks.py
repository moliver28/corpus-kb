def test_all_new_feature_config_blocks_load() -> None:
    from src.config import load_config

    cfg = load_config()
    assert "rerank" in cfg["search"]
    assert "self_query" in cfg["search"]
    assert "matryoshka_dim" in cfg["search"]
    assert cfg["contextual"]["enabled"] is False  # opt-in default per feature table
    assert cfg["judge"]["enabled"] is True  # max-priority always-on feature
    assert cfg["routing"]["enabled"] is True  # max-priority always-on feature

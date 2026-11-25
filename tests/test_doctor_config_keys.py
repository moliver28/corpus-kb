"""Doctor config-keys check tests (PR-9 residue scope).

``config_key_check`` mirrors the env check's config-rot notion for the
user's config files: an unknown leaf key is WARN, the defaults-only file
and a missing user file are OK, and overlays are scanned too.
"""

from __future__ import annotations

import yaml

from corpus_kb._setup import doctor_config_keys
from corpus_kb.config import get_default_config


def _patch_paths(monkeypatch, user_config, overlays=()):
    monkeypatch.setattr(doctor_config_keys, "config_file_path", lambda: user_config)
    monkeypatch.setattr(doctor_config_keys, "config_overlay_paths", lambda: list(overlays))


def test_ok_when_no_user_config(monkeypatch):
    _patch_paths(monkeypatch, None)
    check = doctor_config_keys.config_key_check()
    assert check.status == "ok"


def test_ok_on_defaults_only_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(get_default_config()), encoding="utf-8")
    _patch_paths(monkeypatch, path)
    check = doctor_config_keys.config_key_check()
    assert check.status == "ok"


def test_warn_on_unknown_key(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    config = get_default_config()
    config["retrieval"]["typo_knob"] = 3
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _patch_paths(monkeypatch, path)
    check = doctor_config_keys.config_key_check()
    assert check.status == "warn"
    assert "retrieval.typo_knob" in check.detail
    assert check.fix


def test_legacy_alias_key_not_warned(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"search": {"hnsw": {"ef_search": 200}}}), encoding="utf-8")
    _patch_paths(monkeypatch, path)
    check = doctor_config_keys.config_key_check()
    assert check.status == "ok"


def test_overlay_scanned_and_warned(tmp_path, monkeypatch):
    overlay = tmp_path / "study.yaml"
    overlay.write_text(yaml.safe_dump({"server": {"nope": 1}}), encoding="utf-8")
    _patch_paths(monkeypatch, None, overlays=[overlay])
    check = doctor_config_keys.config_key_check()
    assert check.status == "warn"
    assert "server.nope" in check.detail

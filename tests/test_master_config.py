"""Master topology config defaults (W5-1): the ten master.* leaves.

The keys must exist in ``get_default_config()`` with the plan's exact
values, and the ``CORPUS_KB_MASTER_ADOPT`` override must be a known
variable (the lockstep gate proves the packaged yaml matches leaf for
leaf; see tests/test_config_lockstep.py).
"""

from __future__ import annotations

from corpus_kb.config import KNOWN_CORPUS_KB_ENV_VARS, get_default_config


def test_master_defaults() -> None:
    cfg = get_default_config()
    master = cfg["master"]
    assert master["workspace_root"] == ""
    assert master["adopt_on_start"] is True
    assert master["adopt_skip"] == []
    assert master["scan_interval_s"] == 900
    assert master["switch_window_s"] == 1800
    assert master["idle_window_s"] == 7200


def test_master_backup_defaults() -> None:
    cfg = get_default_config()
    assert cfg["master"]["backup"] == {
        "enabled": True,
        "schedule": "daily",
        "time_local": "03:00",
        "target_dir": "backups",
    }


def test_master_adopt_env_var_not_yet_mapped() -> None:
    # adopt_on_start gets a reader in W8; no env mapping until then (the
    # no-dead-mappings rule). The matrix rows stay: they are the
    # known-key inventory validate_configs accepts.
    assert "CORPUS_KB_MASTER_ADOPT" not in KNOWN_CORPUS_KB_ENV_VARS

"""The calibrate apply op (issue #200): dry-run purity, one handler, typed refusals.

Every test runs offline: the world is a tmp profiles directory with one
profile, its lock, and a small config.yaml. The CLI tests drive the real
handler through the Typer app, so the ONE shared function
(``tools.calibrate_tools.calibrate_apply``) is under test on every path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import ConfigError, ValidationError
from corpus_kb.tools.calibrate_tools import calibrate_apply
from corpus_kb.tools.config_edit import apply_keys_to_text

runner = CliRunner()

PROFILE_YAML = "data_type: transcript\n"
LOCK: dict[str, Any] = {
    "profile": "parity",
    "outcome": "success",
    "chosen": "rrf10",
    "proposed_overrides": {"retrieval.rrf_k": 10, "reranking.enabled": True},
}
CONFIG_TEXT = (
    "# user config\nretrieval:\n  rrf_k: 60  # fusion constant\n  vector_candidates: 200\n"
)


def _write_lock(profiles: Path, **overrides: Any) -> None:
    lock = dict(LOCK)
    # Hash the file's REAL bytes: write_text may translate newlines, and
    # the lock aid compares against the on-disk bytes.
    lock["profile_sha256"] = hashlib.sha256((profiles / "parity.yaml").read_bytes()).hexdigest()
    lock.update(overrides)
    (profiles / "parity.lock.json").write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """One profile + lock + config file; every config seam points at tmp."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "parity.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    _write_lock(profiles)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_TEXT, encoding="utf-8")

    from corpus_kb import config as config_module

    monkeypatch.setattr(config_module, "config_file_path", lambda: config_path)
    monkeypatch.setattr(config_module, "load_config", lambda: {"profiles": {"dir": str(profiles)}})
    return {"profiles": profiles, "config": config_path}


# ---------------------------------------------------------------------------
# The op: dry-run purity and the write path
# ---------------------------------------------------------------------------


async def test_dry_run_leaves_config_byte_identical(world: dict[str, Path]) -> None:
    before = world["config"].read_bytes()
    envelope = await calibrate_apply("parity")
    assert world["config"].read_bytes() == before
    assert envelope["status"] == "success"
    body = envelope["result"]
    assert body == {
        "profile": "parity",
        "outcome": "success",
        "chosen": "rrf10",
        "mode": "dry_run",
        "config_path": str(world["config"]),
        "changes": [
            {"key": "reranking.enabled", "current": None, "proposed": True, "changed": True},
            {"key": "retrieval.rrf_k", "current": 60, "proposed": 10, "changed": True},
        ],
        "applied_count": 0,
    }


async def test_write_mutates_only_the_lock_keys(world: dict[str, Path]) -> None:
    envelope = await calibrate_apply("parity", write=True)
    body = envelope["result"]
    assert body["mode"] == "write"
    assert body["applied_count"] == 2
    text = world["config"].read_text(encoding="utf-8")
    assert "  rrf_k: 10  # fusion constant\n" in text  # value + inline comment kept
    assert "  vector_candidates: 200\n" in text  # sibling line byte-identical
    assert "reranking:\n  enabled: true\n" in text  # missing block appended
    assert "# user config\n" in text  # header comment kept


async def test_write_is_idempotent(world: dict[str, Path]) -> None:
    await calibrate_apply("parity", write=True)
    after_first = world["config"].read_bytes()
    envelope = await calibrate_apply("parity", write=True)
    assert world["config"].read_bytes() == after_first
    body = envelope["result"]
    assert body["applied_count"] == 0
    assert all(not row["changed"] for row in body["changes"])


# ---------------------------------------------------------------------------
# Typed refusals (no raw raises; every path is a corpus_kb.errors class)
# ---------------------------------------------------------------------------


async def test_unknown_profile_target_is_a_typed_error(world: dict[str, Path]) -> None:
    with pytest.raises(ValidationError) as excinfo:
        await calibrate_apply("ghost")
    assert excinfo.value.cli_exit == 1
    assert "ghost" in str(excinfo.value)


async def test_missing_lock_is_a_typed_error(world: dict[str, Path]) -> None:
    (world["profiles"] / "parity.lock.json").unlink()
    with pytest.raises(ValidationError):
        await calibrate_apply("parity")


async def test_unknown_recipe_key_is_refused_by_the_allow_list(
    world: dict[str, Path],
) -> None:
    _write_lock(world["profiles"], proposed_overrides={"retrieval.magic_knob": 5})
    with pytest.raises(ValidationError) as excinfo:
        await calibrate_apply("parity")
    assert "magic_knob" in str(excinfo.value)
    # Nothing was written: the refusal happens before the diff.
    assert world["config"].read_text(encoding="utf-8") == CONFIG_TEXT


async def test_stale_profile_refuses_through_the_lock_check(world: dict[str, Path]) -> None:
    edited = "data_type: transcript  # edited\n"
    (world["profiles"] / "parity.yaml").write_text(edited, encoding="utf-8")
    with pytest.raises(ValidationError):
        await calibrate_apply("parity")


async def test_non_success_outcome_refuses(world: dict[str, Path]) -> None:
    _write_lock(world["profiles"], outcome="no_significant_winner")
    with pytest.raises(ValidationError) as excinfo:
        await calibrate_apply("parity", write=True)
    assert "no_significant_winner" in str(excinfo.value)


async def test_lock_without_overrides_refuses(world: dict[str, Path]) -> None:
    lock = json.loads((world["profiles"] / "parity.lock.json").read_text(encoding="utf-8"))
    del lock["proposed_overrides"]
    (world["profiles"] / "parity.lock.json").write_text(
        json.dumps(lock, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        await calibrate_apply("parity")


async def test_unreadable_yaml_config_refuses(world: dict[str, Path]) -> None:
    world["config"].write_text("retrieval: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        await calibrate_apply("parity")
    assert excinfo.value.cli_exit == 3


async def test_non_mapping_config_refuses(world: dict[str, Path]) -> None:
    world["config"].write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        await calibrate_apply("parity")
    assert "mapping" in str(excinfo.value)


async def test_missing_config_file_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "parity.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    _write_lock(profiles)
    from corpus_kb import config as config_module

    monkeypatch.setattr(config_module, "config_file_path", lambda: None)
    with pytest.raises(ConfigError) as excinfo:
        await calibrate_apply("parity", config={"profiles": {"dir": str(profiles)}})
    assert excinfo.value.cli_exit == 3


async def test_unreadable_lock_json_is_a_typed_error(world: dict[str, Path]) -> None:
    (world["profiles"] / "parity.lock.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValidationError):
        await calibrate_apply("parity")


# ---------------------------------------------------------------------------
# The CLI adapter: same handler, honest exits, nothing on disk by default
# ---------------------------------------------------------------------------


def test_cli_dry_run_writes_nothing_and_shows_the_diff(world: dict[str, Path]) -> None:
    before = world["config"].read_bytes()
    result = runner.invoke(app, ["calibrate", "apply", "parity"])
    assert result.exit_code == 0, result.output
    assert world["config"].read_bytes() == before
    assert "retrieval.rrf_k: 60 -> 10" in result.output
    assert "--write" in result.output


def test_cli_write_hits_the_one_handler(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import corpus_kb.tools.calibrate_tools as module

    calls: list[dict[str, Any]] = []
    real = module.calibrate_apply

    async def spy(profile: str, write: bool = False, config: Any = None) -> dict[str, object]:
        calls.append({"profile": profile, "write": write})
        return await real(profile, write=write, config=config)

    monkeypatch.setattr(module, "calibrate_apply", spy)
    result = runner.invoke(app, ["calibrate", "apply", "parity", "--write"])
    assert result.exit_code == 0, result.output
    assert calls == [{"profile": "parity", "write": True}]
    assert "2 change(s) written" in result.output


def test_cli_json_prints_the_exact_envelope(world: dict[str, Path]) -> None:
    result = runner.invoke(app, ["calibrate", "apply", "parity", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "success"
    assert envelope["result"]["mode"] == "dry_run"


def test_cli_unknown_target_exits_one(world: dict[str, Path]) -> None:
    result = runner.invoke(app, ["calibrate", "apply", "ghost"])
    assert result.exit_code == 1
    assert "ERROR:" in result.output
    assert "Name a profile that exists" in result.output


def test_cli_missing_config_exits_three(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_kb import config as config_module

    monkeypatch.setattr(config_module, "config_file_path", lambda: None)
    result = runner.invoke(app, ["calibrate", "apply", "parity"])
    assert result.exit_code == 3
    assert "ERROR:" in result.output


def test_cli_flag_conflict_exits_one(world: dict[str, Path]) -> None:
    result = runner.invoke(app, ["calibrate", "apply", "parity", "--write", "--dry-run"])
    assert result.exit_code == 1
    assert "cannot be used together" in result.output


def test_cli_help_lists_both_flags() -> None:
    result = runner.invoke(app, ["calibrate", "apply", "--help"])
    assert result.exit_code == 0, result.output
    assert "--write" in result.output
    assert "--dry-run" in result.output


# ---------------------------------------------------------------------------
# The targeted text edit: byte-preservation contract
# ---------------------------------------------------------------------------


def test_apply_to_text_inserts_into_an_existing_block() -> None:
    text = "retrieval:\n  vector_candidates: 200\n"
    out = apply_keys_to_text(text, {"retrieval.rrf_k": 10})
    assert out == "retrieval:\n  vector_candidates: 200\n  rrf_k: 10\n"


def test_apply_to_text_preserves_comments_and_unrelated_blocks() -> None:
    text = (
        "# header\n"
        "retrieval:\n"
        "  rrf_k: 60  # fusion constant\n"
        "\n"
        "embedding:\n"
        "  model: nomic-embed-text  # keep me\n"
    )
    out = apply_keys_to_text(text, {"retrieval.rrf_k": 10})
    assert out == (
        "# header\n"
        "retrieval:\n"
        "  rrf_k: 10  # fusion constant\n"
        "\n"
        "embedding:\n"
        "  model: nomic-embed-text  # keep me\n"
    )


def test_apply_to_text_appends_a_missing_block_without_trailing_newline() -> None:
    out = apply_keys_to_text("retrieval:\n  rrf_k: 60", {"reranking.enabled": True})
    assert out == "retrieval:\n  rrf_k: 60\nreranking:\n  enabled: true\n"


def test_apply_to_text_inserts_into_an_empty_block() -> None:
    out = apply_keys_to_text("retrieval:\nembedding:\n  model: m\n", {"retrieval.rrf_k": 10})
    assert out == "retrieval:\n  rrf_k: 10\nembedding:\n  model: m\n"


def test_apply_to_text_emits_string_values_yaml_safely() -> None:
    # Plain words stay bare; a word that would parse as a bool gets quotes.
    plain = apply_keys_to_text(CONFIG_TEXT, {"retrieval.rrf_k": "auto"})
    assert "  rrf_k: auto  # fusion constant\n" in plain
    quoted = apply_keys_to_text(CONFIG_TEXT, {"retrieval.rrf_k": "true"})
    assert "  rrf_k: 'true'  # fusion constant\n" in quoted


def test_apply_to_text_is_deterministic() -> None:
    updates = {"retrieval.rrf_k": 10, "reranking.enabled": True}
    one = apply_keys_to_text(CONFIG_TEXT, updates)
    two = apply_keys_to_text(CONFIG_TEXT, dict(reversed(list(updates.items()))))
    assert one == two

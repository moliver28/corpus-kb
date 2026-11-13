"""The calibrate apply op (issue #200): dry-run purity, one handler, typed refusals.

Every test runs offline: the world is a tmp profiles directory with one
profile, its lock, and a small config.yaml. The CLI tests drive the real
handler through the Typer app, so the ONE shared function
(``tools.calibrate_tools.calibrate_apply``) is under test on every path.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import ConfigError, ValidationError
from corpus_kb.tools.calibrate_tools import calibrate_apply
from corpus_kb.tools.config_edit import apply_keys_to_text, apply_keys_tracked

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
            {
                "key": "reranking.enabled",
                "current": None,
                "proposed": True,
                "changed": True,
                "edit": "created",
            },
            {
                "key": "retrieval.rrf_k",
                "current": 60,
                "proposed": 10,
                "changed": True,
                "edit": "replaced",
            },
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


async def test_write_oserror_is_a_typed_config_error(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A write fault (read-only dir, full disk) must come out as the typed
    # config error, never as a raw OSError (which the CLI would render as
    # a database fault).
    import corpus_kb.tools.calibrate_tools as module

    def boom(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_atomic", boom)
    with pytest.raises(ConfigError) as excinfo:
        await calibrate_apply("parity", write=True)
    assert excinfo.value.cli_exit == 3
    assert "Cannot write the config file" in str(excinfo.value)


def test_cli_write_oserror_names_the_config_fault(
    world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import corpus_kb.tools.calibrate_tools as module

    def boom(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(module, "write_atomic", boom)
    result = runner.invoke(app, ["calibrate", "apply", "parity", "--write"])
    assert result.exit_code == 3, result.output
    assert "Cannot write the config file" in result.output
    assert "cannot reach the database" not in result.output


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
    # Rich styles the help on some runners (ANSI between the flag chars),
    # so strip the codes the way test_cli_tags does before the checks.
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    result = runner.invoke(app, ["calibrate", "apply", "--help"])
    assert result.exit_code == 0, result.output
    flat = ansi.sub("", result.output)
    assert "--write" in flat
    assert "--dry-run" in flat


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


# ---------------------------------------------------------------------------
# Routing locks (W5): two-level dot paths through the SAME apply flow
# ---------------------------------------------------------------------------

ROUTING_OVERRIDES: dict[str, Any] = {
    "analysis.routing.confidence_threshold": 0.45,
    "analysis.routing.margin": 0.05,
}
ROUTING_CONFIG_TEXT = (
    "# user config\n"
    "analysis:\n"
    "  # question router knobs\n"
    "  routing:\n"
    "    confidence_threshold: 0.55\n"
    "    margin: 0.10\n"
    "retrieval:\n"
    "  rrf_k: 60\n"
)


@pytest.fixture
def routing_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """One profile + routing lock + a nested routing block in config.yaml."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "parity.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    _write_lock(
        profiles,
        chosen="t045_m05",
        proposed_overrides=dict(ROUTING_OVERRIDES),
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(ROUTING_CONFIG_TEXT, encoding="utf-8")

    from corpus_kb import config as config_module

    monkeypatch.setattr(config_module, "config_file_path", lambda: config_path)
    monkeypatch.setattr(config_module, "load_config", lambda: {"profiles": {"dir": str(profiles)}})
    return {"profiles": profiles, "config": config_path}


async def test_routing_lock_dry_run_diffs_the_nested_leaves(
    routing_world: dict[str, Path],
) -> None:
    """The diff reads the nested current values; a dry run writes nothing."""
    before = routing_world["config"].read_bytes()
    envelope = await calibrate_apply("parity")
    assert routing_world["config"].read_bytes() == before
    body = envelope["result"]
    changes = body["changes"]
    assert isinstance(changes, list)
    assert changes == [
        {
            "key": "analysis.routing.confidence_threshold",
            "current": 0.55,
            "proposed": 0.45,
            "changed": True,
            "edit": "replaced",
        },
        {
            "key": "analysis.routing.margin",
            "current": 0.10,
            "proposed": 0.05,
            "changed": True,
            "edit": "replaced",
        },
    ]


async def test_routing_lock_write_edits_only_the_nested_lines(
    routing_world: dict[str, Path],
) -> None:
    """The write moves the two leaf lines; every other byte survives."""
    envelope = await calibrate_apply("parity", write=True)
    body = envelope["result"]
    assert body["applied_count"] == 2
    text = routing_world["config"].read_text(encoding="utf-8")
    assert text == (
        "# user config\n"
        "analysis:\n"
        "  # question router knobs\n"
        "  routing:\n"
        "    confidence_threshold: 0.45\n"
        "    margin: 0.05\n"
        "retrieval:\n"
        "  rrf_k: 60\n"
    )


def test_apply_to_text_edits_a_nested_block_in_place() -> None:
    """Two-level keys: the leaf edits inside the inner block, not flat."""
    out = apply_keys_to_text(ROUTING_CONFIG_TEXT, dict(ROUTING_OVERRIDES))
    assert out == (
        "# user config\n"
        "analysis:\n"
        "  # question router knobs\n"
        "  routing:\n"
        "    confidence_threshold: 0.45\n"
        "    margin: 0.05\n"
        "retrieval:\n"
        "  rrf_k: 60\n"
    )


def test_apply_to_text_creates_a_missing_nested_block() -> None:
    """A config without analysis.routing gains the whole nested chain."""
    out = apply_keys_to_text(
        "retrieval:\n  rrf_k: 60\n",
        {"analysis.routing.confidence_threshold": 0.5},
    )
    assert out == (
        "retrieval:\n  rrf_k: 60\nanalysis:\n  routing:\n    confidence_threshold: 0.5\n"
    )


def test_apply_to_text_ignores_a_deeper_same_name_block() -> None:
    """A deeper-nested sibling (labeling.routing) never hijacks the edit.

    Child headers must sit at the parent block's member column: a
    ``routing:`` block two levels deeper is not ``analysis.routing``,
    so the edit CREATES the missing chain instead.
    """
    text = (
        "analysis:\n"
        "  labeling:\n"
        "    routing:\n"
        "      confidence_threshold: 0.9\n"
        "    enabled: false\n"
    )
    out = apply_keys_to_text(text, {"analysis.routing.confidence_threshold": 0.45})
    assert out == (
        "analysis:\n"
        "  labeling:\n"
        "    routing:\n"
        "      confidence_threshold: 0.9\n"
        "    enabled: false\n"
        "  routing:\n"
        "    confidence_threshold: 0.45\n"
    )


def test_apply_to_text_edits_only_the_exact_column_key() -> None:
    """A deeper key with the same name is not the edit target.

    The scan anchors to the block's member column: a same-named key one
    level deeper (and earlier in the file) keeps its value.
    """
    text = (
        "analysis:\n"
        "  routing:\n"
        "    deeper:\n"
        "      confidence_threshold: 9\n"
        "    confidence_threshold: 0.55\n"
    )
    out = apply_keys_to_text(text, {"analysis.routing.confidence_threshold": 0.45})
    assert out == (
        "analysis:\n"
        "  routing:\n"
        "    deeper:\n"
        "      confidence_threshold: 9\n"
        "    confidence_threshold: 0.45\n"
    )


# ---------------------------------------------------------------------------
# The per-key edit report (issue #341): the no-replace outcome is visible
# ---------------------------------------------------------------------------


def test_apply_keys_tracked_reports_a_no_replace_insert() -> None:
    """A key at a deeper indent than the member column is not replaced.

    The anchor reads the block's member column, so a hand-edited deeper
    line never matches: the edit inserts a fresh line, the stale line
    stays, and the receipt says ``inserted``.
    """
    text = "analysis:\n  routing:\n    deeper:\n      confidence_threshold: 9\n    margin: 0.10\n"
    new_text, edits = apply_keys_tracked(text, {"analysis.routing.confidence_threshold": 0.45})
    assert edits == {"analysis.routing.confidence_threshold": "inserted"}
    assert "      confidence_threshold: 9\n" in new_text  # the stale line stays
    assert "    confidence_threshold: 0.45\n" in new_text  # the fresh line is live


def test_apply_keys_tracked_reports_replaced_and_created() -> None:
    """One report per key: the in-place edit and the fresh block differ."""
    new_text, edits = apply_keys_tracked(
        CONFIG_TEXT, {"retrieval.rrf_k": 10, "reranking.enabled": True}
    )
    assert edits == {"retrieval.rrf_k": "replaced", "reranking.enabled": "created"}
    assert "  rrf_k: 10  # fusion constant\n" in new_text
    assert "reranking:\n  enabled: true\n" in new_text


NO_REPLACE_CONFIG_TEXT = (
    "# user config\n"
    "analysis:\n"
    "  routing:\n"
    "    deeper:\n"
    "      confidence_threshold: 9\n"
    "    margin: 0.10\n"
)


@pytest.fixture
def no_replace_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """One profile + routing lock over a config whose target key sits deeper.

    The lock carries the same margin value the file already has, so one
    row is a real change (insert) and one row needs no edit at all.
    """
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "parity.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    _write_lock(
        profiles,
        chosen="t045_m05",
        proposed_overrides={
            "analysis.routing.confidence_threshold": 0.45,
            "analysis.routing.margin": 0.10,
        },
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(NO_REPLACE_CONFIG_TEXT, encoding="utf-8")

    from corpus_kb import config as config_module

    monkeypatch.setattr(config_module, "config_file_path", lambda: config_path)
    monkeypatch.setattr(config_module, "load_config", lambda: {"profiles": {"dir": str(profiles)}})
    return {"profiles": profiles, "config": config_path}


async def test_no_replace_edit_shows_in_the_change_rows(
    no_replace_world: dict[str, Path],
) -> None:
    """The dry run reports the insert; the untouched row reports ``none``."""
    before = no_replace_world["config"].read_bytes()
    envelope = await calibrate_apply("parity")
    assert no_replace_world["config"].read_bytes() == before
    changes = envelope["result"]["changes"]
    assert isinstance(changes, list)
    rows = [dict(row) for row in changes]
    assert rows[0]["edit"] == "inserted"  # the confidence_threshold insert
    assert rows[1]["edit"] == "none"  # the margin value already matches


async def test_no_replace_write_keeps_the_stale_line(
    no_replace_world: dict[str, Path],
) -> None:
    """The write inserts the fresh line; the receipt and file agree."""
    envelope = await calibrate_apply("parity", write=True)
    body = envelope["result"]
    assert body["applied_count"] == 1
    text = no_replace_world["config"].read_text(encoding="utf-8")
    assert "      confidence_threshold: 9\n" in text  # the stale line stays
    assert "    confidence_threshold: 0.45\n" in text  # the parsed value is live


def test_cli_receipt_marks_a_no_replace_edit(no_replace_world: dict[str, Path]) -> None:
    """The plain receipt says when a line was not replaced."""
    result = runner.invoke(app, ["calibrate", "apply", "parity"])
    assert result.exit_code == 0, result.output
    assert "no replace" in result.output
    assert "(already applied)" in result.output

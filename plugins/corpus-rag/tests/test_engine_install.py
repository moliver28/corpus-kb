"""Tests for plugins/corpus-rag/engine_install.py (the engine install command).

Stdlib only. The git and uv subprocess calls are faked; no test clones,
syncs, or touches the network.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))


def load() -> ModuleType:
    sys.modules.pop("engine_install", None)
    return importlib.import_module("engine_install")


def load_bin() -> ModuleType:
    """Load the extensionless bin/corpus-rag wrapper as a module."""
    loader = importlib.machinery.SourceFileLoader(
        "corpus_rag_bin", str(PLUGIN / "bin" / "corpus-rag")
    )
    spec = importlib.util.spec_from_loader("corpus_rag_bin", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def set_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the engine state dir at a throwaway folder."""
    state = tmp_path / "state"
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(state))
    monkeypatch.delenv("CORPUS_KB_REPO", raising=False)
    return state


def seed_install(state: Path, version: str) -> Path:
    """A finished-looking install folder with a version marker."""
    target = state / "engine"
    target.mkdir(parents=True)
    marker = {"version": version, "tag": f"corpus-rag-v{version}"}
    (target / ".corpus-rag-engine.json").write_text(json.dumps(marker), encoding="utf-8")
    (target / "placeholder.txt").write_text("old install", encoding="utf-8")
    return target


def backup_folders(state: Path) -> list[Path]:
    """Everything kept under <state>/engine-versions, sorted by name."""
    versions = state / "engine-versions"
    if not versions.is_dir():
        return []
    return sorted(versions.iterdir())


def allow_uv(monkeypatch: pytest.MonkeyPatch, mod: ModuleType) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/fake/uv" if name == "uv" else None)


def deny_uv(monkeypatch: pytest.MonkeyPatch, mod: ModuleType) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)


class FakeRunner:
    """Stands in for subprocess.run. A plan maps each command to an outcome."""

    def __init__(self, plan: Callable[[list[str]], tuple[int, str, str]]) -> None:
        self.calls: list[list[str]] = []
        self.plan = plan

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        code, out, err = self.plan(list(cmd))
        return subprocess.CompletedProcess(cmd, code, out, err)


def make_plan(
    tags: list[str], sync_code: int = 0, sync_err: str = ""
) -> Callable[[list[str]], tuple[int, str, str]]:
    listing = "".join(f"abc123\trefs/tags/{tag}\n" for tag in tags)

    def plan(cmd: list[str]) -> tuple[int, str, str]:
        if cmd[:2] == ["git", "ls-remote"]:
            return 0, listing, ""
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)  # clone's side effect
            return 0, "", ""
        if cmd[:1] == ["uv"]:
            return sync_code, "", sync_err
        raise AssertionError(f"unexpected command: {cmd}")

    return plan


def fake_run(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, plan: Callable[..., tuple[int, str, str]]
) -> FakeRunner:
    runner = FakeRunner(plan)
    monkeypatch.setattr(mod.subprocess, "run", runner)
    return runner


# --- install success path -------------------------------------------------


def test_install_success_clones_tag_and_syncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.7.1", "corpus-rag-v0.6.0"]))

    message = mod.run_install(version="0.7.1")

    target = state / "engine"
    assert message.startswith("ok:")
    clone = next(c for c in runner.calls if c[:2] == ["git", "clone"])
    assert clone == [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        "corpus-rag-v0.7.1",
        mod.plugin_repo_url(),
        str(target),
    ]
    sync = next(c for c in runner.calls if c[:1] == ["uv"])
    assert sync == ["uv", "sync", "--frozen", "--no-dev"]
    marker = json.loads((target / ".corpus-rag-engine.json").read_text(encoding="utf-8"))
    assert marker["version"] == "0.7.1"
    assert marker["tag"] == "corpus-rag-v0.7.1"


def test_default_version_comes_from_plugin_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan([f"corpus-rag-v{mod.plugin_version()}"]))

    mod.run_install()

    clone = next(c for c in runner.calls if c[:2] == ["git", "clone"])
    assert f"corpus-rag-v{mod.plugin_version()}" in clone


# --- missing tag ----------------------------------------------------------


def test_missing_tag_lists_nearest_tags_and_never_clones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.6.0", "corpus-rag-v0.5.0"]))

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="9.9.9")

    message = str(excinfo.value)
    assert "corpus-rag-v9.9.9" in message
    assert "corpus-rag-v0.6.0" in message
    assert "not-a-release" not in message
    assert all(c[:2] != ["git", "clone"] for c in runner.calls)
    assert not (state / "engine").exists()


def test_missing_tag_with_prior_install_leaves_it_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tag check runs before anything moves, so a working install survives."""
    state = set_state(monkeypatch, tmp_path)
    seed_install(state, "0.7.0")
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.6.0"]))

    with pytest.raises(mod.EngineInstallError):
        mod.run_install(version="0.7.1")

    target = state / "engine"
    assert mod.installed_version(target) == "0.7.0"
    assert (target / "placeholder.txt").exists()
    assert backup_folders(state) == []
    assert all(c[:2] != ["git", "clone"] for c in runner.calls)


def test_no_release_tags_at_all_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(mod, monkeypatch, make_plan(["unrelated-tag"]))

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="0.7.1")

    assert "No corpus-rag-v* tags" in str(excinfo.value)


# --- idempotence and --force ----------------------------------------------


def test_already_installed_is_a_no_op_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    version = load().plugin_version()
    seed_install(state, version)
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan([f"corpus-rag-v{version}"]))

    message = mod.run_install()

    assert "already installed" in message
    assert runner.calls == []
    assert (state / "engine" / "placeholder.txt").exists()


def test_force_reinstalls_and_keeps_rollback_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    version = load().plugin_version()
    seed_install(state, version)
    mod = load()
    allow_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan([f"corpus-rag-v{version}"]))

    message = mod.run_install(force=True)

    assert message.startswith("ok:")
    backups = [p for p in backup_folders(state) if p.name.startswith(f"{version}-")]
    assert len(backups) == 1
    assert (backups[0] / "placeholder.txt").exists()
    assert any(c[:2] == ["git", "clone"] for c in runner.calls)
    marker = json.loads(
        (state / "engine" / ".corpus-rag-engine.json").read_text(encoding="utf-8")
    )
    assert marker["version"] == version


def test_upgrade_keeps_older_version_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    seed_install(state, "0.0.1")
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(mod, monkeypatch, make_plan([f"corpus-rag-v{mod.plugin_version()}"]))

    message = mod.run_install()

    assert message.startswith("ok:")
    backups = [p for p in backup_folders(state) if p.name.startswith("0.0.1-")]
    assert len(backups) == 1
    assert (backups[0] / "placeholder.txt").exists()


def test_backup_move_oserror_surfaces_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked old install (OSError on move) is a typed error, not a crash."""
    state = set_state(monkeypatch, tmp_path)
    seed_install(state, "0.7.0")
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.7.1"]))

    def deny_move(src: object, dst: object) -> None:
        raise PermissionError(13, "file held open")

    monkeypatch.setattr(mod.shutil, "move", deny_move)

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="0.7.1")

    assert "Could not move the old install" in str(excinfo.value)
    assert (state / "engine" / "placeholder.txt").exists()
    assert backup_folders(state) == []


def test_install_over_unmarked_folder_keeps_it_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    target = state / "engine"
    target.mkdir(parents=True)
    (target / "placeholder.txt").write_text("manual clone", encoding="utf-8")
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.7.1"]))

    message = mod.run_install(version="0.7.1")

    assert message.startswith("ok:")
    backups = list((state / "engine-versions").iterdir())
    assert len(backups) == 1
    assert backups[0].name.startswith("unknown-")
    assert (backups[0] / "placeholder.txt").exists()


# --- failure surfacing -----------------------------------------------------


def test_uv_sync_failure_removes_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(
        mod,
        monkeypatch,
        make_plan(["corpus-rag-v0.7.1"], sync_code=1, sync_err="lockfile out of date"),
    )

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="0.7.1")

    message = str(excinfo.value)
    assert "uv sync failed" in message
    assert "lockfile out of date" in message
    assert "removed" in message
    assert not (state / "engine").exists()


def test_failure_after_backup_points_at_the_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a step fails after the old install moved, the message says where it is."""
    state = set_state(monkeypatch, tmp_path)
    seed_install(state, "0.7.0")
    mod = load()
    allow_uv(monkeypatch, mod)
    fake_run(
        mod,
        monkeypatch,
        make_plan(["corpus-rag-v0.7.1"], sync_code=1, sync_err="no space left"),
    )

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="0.7.1")

    message = str(excinfo.value)
    assert "The previous install is safe at" in message
    backups = [p for p in backup_folders(state) if p.name.startswith("0.7.0-")]
    assert len(backups) == 1
    assert (backups[0] / "placeholder.txt").exists()


def test_failed_sync_retry_does_not_accumulate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retries after a failed sync re-clone cleanly; no unknown-* copies pile up."""
    state = set_state(monkeypatch, tmp_path)
    mod = load()
    allow_uv(monkeypatch, mod)
    failing = fake_run(
        mod, monkeypatch, make_plan(["corpus-rag-v0.7.1"], sync_code=1, sync_err="transient")
    )
    with pytest.raises(mod.EngineInstallError):
        mod.run_install(version="0.7.1")
    assert not (state / "engine").exists()
    assert len(failing.calls) > 0

    working = fake_run(mod, monkeypatch, make_plan(["corpus-rag-v0.7.1"]))
    message = mod.run_install(version="0.7.1")

    assert message.startswith("ok:")
    assert (state / "engine" / ".corpus-rag-engine.json").exists()
    assert backup_folders(state) == []
    assert any(c[:2] == ["git", "clone"] for c in working.calls)


def test_missing_uv_fails_before_any_git_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_state(monkeypatch, tmp_path)
    mod = load()
    deny_uv(monkeypatch, mod)
    runner = fake_run(mod, monkeypatch, make_plan([]))

    with pytest.raises(mod.EngineInstallError) as excinfo:
        mod.run_install(version="0.7.1")

    assert "uv" in str(excinfo.value)
    assert runner.calls == []


def test_nearest_tags_orders_by_version_distance() -> None:
    mod = load()
    tags = [
        "corpus-rag-v0.5.0",
        "corpus-rag-v0.9.0",
        "corpus-rag-v0.6.0",
        "not-a-release",
    ]
    assert mod.nearest_tags(tags, "corpus-rag-v0.7.1") == [
        "corpus-rag-v0.6.0",
        "corpus-rag-v0.5.0",
        "corpus-rag-v0.9.0",
        "not-a-release",
    ]


# --- wrapper wiring (bin/corpus-rag) ---------------------------------------


def test_wrapper_dispatches_engine_install(monkeypatch: pytest.MonkeyPatch) -> None:
    binmod = load_bin()
    seen: list[bool] = []
    monkeypatch.setattr(
        binmod.engine_install, "run_install", lambda *, force: seen.append(force) or "ok: fake"
    )
    assert binmod.main(["engine", "install"]) == 0
    assert seen == [False]


def test_wrapper_passes_force_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    binmod = load_bin()
    seen: list[bool] = []
    monkeypatch.setattr(
        binmod.engine_install, "run_install", lambda *, force: seen.append(force) or "ok: fake"
    )
    assert binmod.main(["engine", "install", "--force"]) == 0
    assert seen == [True]


def test_wrapper_maps_error_to_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binmod = load_bin()

    def boom(*, force: bool) -> str:
        raise binmod.engine_install.EngineInstallError("bad thing happened")

    monkeypatch.setattr(binmod.engine_install, "run_install", boom)
    assert binmod.main(["engine", "install"]) == 1
    assert "not ok: bad thing happened" in capsys.readouterr().out

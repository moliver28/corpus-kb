"""Behavior tests for the study registry (issue #351; 0.6.0 pinned).

Covers: slug rule, atomic writes under two writers, the 0.6.0
``projects.json`` import, port allocation, and the 4-step study
selection including the folder's ``.claude/corpus.json``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]

import sys  # noqa: E402

sys.path.insert(0, str(PLUGIN))

import study_registry  # noqa: E402


@pytest.fixture(autouse=True)
def plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "engine-state"))
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_BASE_PORT", raising=False)
    return tmp_path


def test_slug_rule() -> None:
    assert study_registry.slugify("13 Studies!") == "13_studies"
    assert study_registry.slugify("My--Study__x") == "my_study_x"
    assert study_registry.slugify("_edge_") == "edge"
    assert study_registry.database_name("alpha") == "corpus_kb_alpha"
    with pytest.raises(study_registry.RegistryError):
        study_registry.slugify("///")


def test_registry_roundtrip() -> None:
    registry = study_registry.load_registry()
    assert registry == {"active": None, "studies": {}}
    study_registry.register_study(registry, "alpha", "postgresql://x/a", 8010, "postgres")
    study_registry.set_active(registry, "alpha")
    study_registry.save_registry(registry)
    loaded = study_registry.load_registry()
    assert loaded["active"] == "alpha"
    assert loaded["studies"]["alpha"]["port"] == 8010


def test_save_is_atomic_under_two_writers() -> None:
    stop = threading.Event()
    errors: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            if study_registry.registry_path().is_file():
                try:
                    study_registry.load_registry()
                except (study_registry.RegistryError, OSError, ValueError) as exc:
                    errors.append(str(exc))
            time.sleep(0.001)

    def writer(tag: str) -> None:
        for i in range(25):
            registry = study_registry.load_registry()
            study_registry.register_study(
                registry, f"{tag}_{i}", f"postgresql://x/{tag}{i}", 8010, "postgres"
            )
            study_registry.save_registry(registry)

    watcher = threading.Thread(target=reader, daemon=True)
    watcher.start()
    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    watcher.join(timeout=5)
    assert not errors
    path = study_registry.registry_path()
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["studies"]  # the last writer's rows landed whole
    assert not Path(str(path) + ".tmp").exists()
    assert not Path(str(path) + ".lock").exists()


def test_projects_json_import_on_first_run(tmp_path: Path) -> None:
    legacy = study_registry.legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "projects": {
                    "interviews": {
                        "db": "postgresql://corpus_user:pw@localhost:5432/corpus_kb_interviews",
                        "port": 8021,
                        "pid": 4321,
                        "backend": "postgres",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    registry = study_registry.load_registry()
    study = registry["studies"]["interviews"]
    assert study["database_url"].endswith("corpus_kb_interviews")
    assert study["port"] == 8021
    assert study["backend"] == "postgres"
    assert study["pid"] == 4321
    study_registry.save_registry(registry)
    assert study_registry.registry_path().is_file()
    assert legacy.is_file()  # the old file stays


def test_selection_order_folder_then_active(tmp_path: Path) -> None:
    registry = study_registry.load_registry()
    study_registry.register_study(registry, "alpha", "postgresql://x/alpha", 8010, "postgres")
    study_registry.register_study(registry, "beta", "postgresql://x/beta", 8011, "postgres")
    registry["active"] = "alpha"

    folder = tmp_path / "study-folder"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "corpus.json").write_text(
        json.dumps({"study": "beta"}), encoding="utf-8"
    )
    slug, _ = study_registry.resolve_study(registry, cwd=folder)
    assert slug == "beta"
    # Step 1 beats the folder marker.
    slug, _ = study_registry.resolve_study(registry, explicit="alpha", cwd=folder)
    assert slug == "alpha"
    # Step 3: the active study when no folder names one.
    slug, _ = study_registry.resolve_study(registry, cwd=tmp_path)
    assert slug == "alpha"
    # Step 4: no study anywhere means the hint.
    with pytest.raises(study_registry.RegistryError) as excinfo:
        study_registry.resolve_study({"active": None, "studies": {}}, cwd=tmp_path)
    assert study_registry.NO_STUDY_MESSAGE in str(excinfo.value)


def test_folder_slug_must_be_registered(tmp_path: Path) -> None:
    registry = study_registry.load_registry()
    study_registry.register_study(registry, "alpha", "postgresql://x/alpha", 8010, "postgres")
    registry["active"] = "alpha"
    folder = tmp_path / "unknown-folder"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "corpus.json").write_text(
        json.dumps({"study": "ghost"}), encoding="utf-8"
    )
    slug, _ = study_registry.resolve_study(registry, cwd=folder)
    assert slug == "alpha"


def test_allocate_port_skips_taken_and_study_ports() -> None:
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 8010))
    held.listen(1)
    registry = study_registry.load_registry()
    assert study_registry.allocate_port(registry, base=8010) == 8011
    held.close()
    # A port a registered study already holds never gets handed out twice.
    study_registry.register_study(registry, "alpha", "postgresql://x", 8011, "postgres")
    port = study_registry.allocate_port(registry, base=8011)
    assert port != 8011


def test_set_active_unknown_study_fails() -> None:
    with pytest.raises(study_registry.RegistryError):
        study_registry.set_active(study_registry.empty_registry(), "ghost")

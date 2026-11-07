"""Behavior tests for the shared folder-state function (issue #352)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import folder_state  # noqa: E402


def write_corpus(root: Path, **keys: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".claude" / "corpus.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(keys), encoding="utf-8")
    return marker


def fake_registry(tmp_path: Path, slug: str) -> dict:
    return {
        "active": None,
        "studies": {slug: {"database_url": "x", "port": 8010, "backend": "postgres"}},
    }


def state(tmp_path: Path, monkeypatch, start: Path, registry: dict | None = None):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return folder_state.folder_state(start, registry=registry)


def make_transcripts(folder: Path) -> None:
    (folder / "interviews").mkdir(parents=True, exist_ok=True)
    (folder / "interviews" / "p1.md").write_text(
        "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# corpus state
# ---------------------------------------------------------------------------


def test_registered_folder_is_corpus(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3", watch=["interviews"])
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "corpus"
    assert result.slug == "q3"
    assert result.root == root.resolve()
    assert result.watch == [root / "interviews"]
    assert result.auto_ingest is True


def test_corpus_found_from_nested_cwd(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3")
    nested = root / "interviews" / "p1"
    nested.mkdir(parents=True)
    result = state(tmp_path, monkeypatch, nested, fake_registry(tmp_path, "q3"))
    assert result.state == "corpus"
    assert result.root == root.resolve()


def test_unregistered_slug_is_not_corpus(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="ghost")
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "other"))
    assert result.state == "none"


def test_enabled_false_is_off(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3", enabled=False)
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "off"


def test_declined_folder_is_off(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3")
    data_dir = tmp_path / "plugin-data"
    marks = data_dir / "marks"
    marks.mkdir(parents=True)
    (marks / folder_state.declined_mark_name(root)).write_text("", encoding="utf-8")
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "off"


def test_custom_excludes_and_auto_ingest_off(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3", exclude=["secret/**"], auto_ingest=False)
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.exclude == ["secret/**"]
    assert result.auto_ingest is False


def test_default_excludes_used_without_custom_list(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3")
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert list(result.exclude) == list(folder_state.DEFAULT_EXCLUDES)


# ---------------------------------------------------------------------------
# research state: no corpus.json, but the folder has transcripts
# ---------------------------------------------------------------------------


def test_transcript_folder_without_corpus_is_research(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "plain"
    make_transcripts(root)
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "research"


def test_plain_folder_without_corpus_is_none(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "none"


def test_unregistered_corpus_with_transcripts_stays_silent(
    tmp_path: Path, monkeypatch
) -> None:
    """A named-but-unregistered study folder never offers the setup talk."""
    root = tmp_path / "study"
    write_corpus(root, slug="ghost")
    make_transcripts(root)
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "other"))
    assert result.state == "none"


def test_broken_corpus_json_is_none(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    root.mkdir()
    marker = root / ".claude" / "corpus.json"
    marker.parent.mkdir()
    marker.write_text("{not json", encoding="utf-8")
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "none"


# ---------------------------------------------------------------------------
# Defaults and helpers
# ---------------------------------------------------------------------------


def test_watch_defaults_to_the_folder_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3")
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.watch == [root]


def test_watch_dirs_skip_missing_folders(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "study"
    write_corpus(root, slug="q3", watch=["real", "missing"])
    (root / "real").mkdir()
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.watch_dirs() == [root / "real"]


def test_declined_mark_name_is_stable_per_folder(tmp_path: Path) -> None:
    a = folder_state.declined_mark_name(tmp_path / "one")
    b = folder_state.declined_mark_name(tmp_path / "two")
    assert a == folder_state.declined_mark_name(tmp_path / "one")
    assert a != b


def test_engine_repo_is_never_corpus(tmp_path: Path, monkeypatch) -> None:
    """Always none: the engine repo itself, even with a corpus.json in it."""
    root = tmp_path / "engine-repo"
    write_corpus(root, slug="q3")
    (root / "src" / "corpus_kb").mkdir(parents=True)
    result = state(tmp_path, monkeypatch, root, fake_registry(tmp_path, "q3"))
    assert result.state == "none"

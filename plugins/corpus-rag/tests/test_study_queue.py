"""Behavior tests for the drain command and auto-drain (issue #352).

The op sender and the study resolution are replaced, so no test launches
a real engine server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import folder_state  # noqa: E402
import ingest_queue  # noqa: E402
import study_queue  # noqa: E402


class Args:
    def __init__(self, study_slug=None, approve=False) -> None:
        self.study_slug = study_slug
        self.approve = approve


def corpus_folder(tmp_path: Path, monkeypatch, auto_ingest=True) -> Path:
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "corpus.json").write_text(
        f'{{"slug": "q3", "auto_ingest": {"true" if auto_ingest else "false"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        folder_state,
        "folder_state",
        lambda start=None, registry=None, scan=True: folder_state.FolderState(
            "corpus",
            root,
            {"slug": "q3"},
            slug="q3",
            watch=[root],
            exclude=[],
            auto_ingest=auto_ingest,
        ),
    )
    return root


def patch_scope(monkeypatch, calls: list[str]) -> None:
    import op_runner
    import study_commands

    def fake_scoped(slug_arg, ensure_server=True):
        calls.append(f"scoped:{slug_arg}")
        return "corpus-kb", "q3", {"port": 8010}

    def fake_inventory(cli):
        calls.append("inventory")
        return {"operations": []}

    sent = []

    def fake_run_op(cli, name, pairs, base_url, **kwargs):
        sent.append((name, pairs))
        return {"status": "success"}

    monkeypatch.setattr(study_commands, "scoped_study", fake_scoped)
    monkeypatch.setattr(op_runner, "load_inventory", fake_inventory)
    monkeypatch.setattr(op_runner, "run_op", fake_run_op)
    return sent


def test_drain_sends_the_folder_queue(tmp_path: Path, monkeypatch, capsys) -> None:
    root = corpus_folder(tmp_path, monkeypatch)
    file = root / "notes.md"
    file.write_text("x", encoding="utf-8")
    ingest_queue.enqueue(root, file, kind="file")
    calls: list[str] = []
    sent = patch_scope(monkeypatch, calls)
    assert study_queue.cmd_drain(Args()) == 0
    assert [name for name, _ in sent] == ["queue_enqueue"]
    out = capsys.readouterr().out
    assert "1 ingested" in out
    assert ingest_queue.queue_rows(root) == []


def test_drain_needs_a_study_folder(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        folder_state,
        "folder_state",
        lambda start=None, registry=None, scan=True: folder_state.FolderState(
            "none", tmp_path
        ),
    )
    assert study_queue.cmd_drain(Args()) == 1
    assert "No study folder" in capsys.readouterr().out


def test_drain_without_approve_when_auto_ingest_off(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    corpus_folder(tmp_path, monkeypatch, auto_ingest=False)
    calls: list[str] = []
    patch_scope(monkeypatch, calls)
    assert study_queue.cmd_drain(Args()) == 1
    assert "--approve" in capsys.readouterr().out
    assert calls == []  # nothing ran


def test_drain_with_approve_when_auto_ingest_off(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = corpus_folder(tmp_path, monkeypatch, auto_ingest=False)
    (root / "notes.md").write_text("x", encoding="utf-8")
    ingest_queue.enqueue(root, root / "notes.md", kind="file")
    calls: list[str] = []
    patch_scope(monkeypatch, calls)
    assert study_queue.cmd_drain(Args(approve=True)) == 0


def test_auto_drain_runs_when_corpus_auto_and_queued(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = corpus_folder(tmp_path, monkeypatch)
    (root / "notes.md").write_text("x", encoding="utf-8")
    ingest_queue.enqueue(root, root / "notes.md", kind="file")
    calls: list[str] = []
    sent = patch_scope(monkeypatch, calls)
    study_queue.auto_drain()
    assert [name for name, _ in sent] == ["queue_enqueue"]
    assert ingest_queue.queue_rows(root) == []
    assert capsys.readouterr().out == ""  # silent


def test_auto_drain_skips_empty_queue(tmp_path: Path, monkeypatch) -> None:
    corpus_folder(tmp_path, monkeypatch)
    calls: list[str] = []
    patch_scope(monkeypatch, calls)
    study_queue.auto_drain()
    assert calls == []


def test_auto_drain_skips_auto_ingest_off(tmp_path: Path, monkeypatch) -> None:
    corpus_folder(tmp_path, monkeypatch, auto_ingest=False)
    calls: list[str] = []
    patch_scope(monkeypatch, calls)
    study_queue.auto_drain()
    assert calls == []


def test_auto_drain_swallows_engine_errors(tmp_path: Path, monkeypatch) -> None:
    root = corpus_folder(tmp_path, monkeypatch)
    (root / "notes.md").write_text("x", encoding="utf-8")
    ingest_queue.enqueue(root, root / "notes.md", kind="file")
    import study_commands

    def boom(slug_arg, ensure_server=True):
        raise study_commands.CommandError("engine is down")

    monkeypatch.setattr(study_commands, "scoped_study", boom)
    study_queue.auto_drain()  # must not raise


def test_auto_drain_skip_set_matches_the_issue() -> None:
    assert frozenset(
        {"drain", "list", "provision", "migrate", "backup", "stop"}
    ) == study_queue.AUTO_DRAIN_SKIP


def test_cli_spec_has_drain_and_skip_commands() -> None:
    import cli_spec

    parser = cli_spec.build_parser()
    parsed = parser.parse_args(["drain", "--approve"])
    assert parsed.command == "drain"
    assert parsed.approve is True
    for name in study_queue.AUTO_DRAIN_SKIP:
        assert name in cli_spec.HANDLERS or name == "drain"
    assert "drain" in cli_spec.HANDLERS


def test_broken_sender_keeps_rows(tmp_path: Path, monkeypatch, capsys) -> None:
    root = corpus_folder(tmp_path, monkeypatch)
    file = root / "notes.md"
    file.write_text("x", encoding="utf-8")
    ingest_queue.enqueue(root, file, kind="file")
    import op_runner

    def fail_op(cli, name, pairs, base_url, **kwargs):
        raise op_runner.OpError("server said no")

    monkeypatch.setattr(op_runner, "load_inventory", lambda cli: {"operations": []})
    monkeypatch.setattr(op_runner, "run_op", fail_op)

    class FakeScope:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    import study_commands

    monkeypatch.setattr(
        study_commands,
        "scoped_study",
        lambda slug_arg, ensure_server=True: ("corpus-kb", "q3", {"port": 8010}),
    )
    assert study_queue.cmd_drain(Args()) == 1
    assert ingest_queue.queue_rows(root)  # failure kept
    assert "kept (failed)" in capsys.readouterr().out


def test_never_real_server() -> None:
    """Guard: this suite replaces the sender; assert the seam exists."""
    assert callable(ingest_queue.drain)
    assert callable(study_queue.auto_drain)
    assert callable(study_queue.cmd_drain)


@pytest.mark.parametrize("command", sorted(study_queue.AUTO_DRAIN_SKIP))
def test_skip_commands_are_real_commands(command: str) -> None:
    import cli_spec

    parser = cli_spec.build_parser()
    extra = ["some-slug"] if command in ("provision", "backup") else []
    parsed = parser.parse_args([command, *extra])
    assert parsed.command == command

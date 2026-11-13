"""The generic ``post_search`` extension point and the UXR block (issue #355).

``corpus-rag search`` runs every registered post-search command after
printing hits. The registry is a small JSON file under the plugin data
folder; it knows nothing about UXR. The ``uxr post-search`` command
prints up to three stored rows under the pinned header.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import cli_spec  # noqa: E402
import post_search  # noqa: E402
import uxr_store  # noqa: E402


def setup(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    conn = uxr_store.connect("ux1")
    uxr_store.save_study(
        conn, spec_id="abc123", slug="ux1", ticket="UX1-275", questions=[]
    )
    uxr_store.save_rows(
        conn,
        "abc123",
        [
            {
                "session": "p1",
                "unit_id": "H1",
                "stance": "Supports",
                "quote": "the export worked",
                "quote_class": "exact",
                "rationale": "said it worked",
                "verified": True,
            },
            {
                "session": "p2",
                "unit_id": "RQ1",
                "stance": "the flow needs an undo",
                "quote": "",
                "quote_class": None,
                "rationale": "no quote",
                "verified": True,
            },
        ],
    )
    return tmp_path / "plugin-data"


def run_uxr_post_search(slug: str = "ux1") -> str:
    import uxr_summaries

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = uxr_summaries.cmd_uxr_post_search(
            cli_spec.parse_argv(
                ["uxr", "post-search", "--post-search-json",
                 json.dumps({"slug": slug, "query": "export"})]
            )
        )
    assert code == 0
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_register_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    assert post_search.register("uxr post-search") is True
    assert post_search.register("uxr post-search") is False
    assert post_search.commands() == ["uxr post-search"]
    assert post_search.unregister("uxr post-search") is None
    assert post_search.commands() == []


def test_registry_survives_a_reload(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    post_search.register("uxr post-search")
    again = post_search.commands()
    assert again == ["uxr post-search"]


# ---------------------------------------------------------------------------
# The dispatch
# ---------------------------------------------------------------------------


def test_run_registered_prints_the_registered_command_output(
    tmp_path: Path, monkeypatch
) -> None:
    setup(tmp_path, monkeypatch)
    post_search.register("uxr post-search")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        count = post_search.run_registered("ux1", "export")
    assert count == 1
    assert "Derived insights (model output, not evidence):" in buffer.getvalue()


def test_run_registered_without_a_store_prints_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    setup(tmp_path, monkeypatch)
    post_search.register("uxr post-search")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        count = post_search.run_registered("other-slug", "export")
    assert count == 0
    assert buffer.getvalue() == ""


def test_run_registered_fails_open_on_a_broken_command(
    tmp_path: Path, monkeypatch
) -> None:
    setup(tmp_path, monkeypatch)
    post_search.register("no-such-command")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        count = post_search.run_registered("ux1", "export")
    assert count == 0
    assert buffer.getvalue() == ""


# ---------------------------------------------------------------------------
# The UXR block
# ---------------------------------------------------------------------------


def test_block_header_and_row_format(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    out = run_uxr_post_search()
    assert "Derived insights (model output, not evidence):" in out
    assert "H1: Supports" in out
    assert "[S:p1]" in out
    assert "the export worked" in out


def test_block_prints_at_most_three_rows(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    conn = uxr_store.connect("ux1")
    extra = [
        {
            "session": f"p{n}",
            "unit_id": "A1",
            "stance": "Holds",
            "quote": "",
            "quote_class": None,
            "rationale": "r",
            "verified": True,
        }
        for n in range(2, 6)
    ]
    uxr_store.save_rows(conn, "abc123", extra)
    out = run_uxr_post_search()
    assert out.count("\n  ") <= 3


def test_block_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    assert run_uxr_post_search() == run_uxr_post_search()


# ---------------------------------------------------------------------------
# Search wiring
# ---------------------------------------------------------------------------


def test_search_calls_the_post_search_point(tmp_path: Path, monkeypatch) -> None:
    setup(tmp_path, monkeypatch)
    import study_query

    calls: list[tuple[str, str]] = []

    def fake_run(slug: str, query: str) -> int:
        calls.append((slug, query))
        return 0

    monkeypatch.setattr(study_query.post_search, "run_registered", fake_run)
    args = cli_spec.parse_argv(["search", "export", "-p", "x"])
    monkeypatch.setattr(
        study_query.study_commands, "scoped_study", lambda slug: (None, "x", {})
    )
    monkeypatch.setattr(
        study_query.op_runner,
        "run_op",
        lambda cli, name, pairs, base, inventory=None: {"result": []},
    )
    with contextlib.redirect_stdout(io.StringIO()):
        study_query.cmd_search(args)
    assert calls == [("x", "export")]


def test_register_holds_one_lock_across_read_and_write(
    tmp_path: Path, monkeypatch
) -> None:
    setup(tmp_path, monkeypatch)
    events: list[str] = []
    real_exclusive = post_search.exclusive
    real_save = post_search._save

    @contextlib.contextmanager
    def spy_exclusive(path, wait=5.0):
        events.append("lock")
        with real_exclusive(path, wait=wait):
            yield
        events.append("unlock")

    def spy_save(data: dict) -> None:
        events.append("save")
        real_save(data)

    monkeypatch.setattr(post_search, "exclusive", spy_exclusive)
    monkeypatch.setattr(post_search, "_save", spy_save)
    assert post_search.register("uxr post-search") is True
    assert events == ["lock", "save", "unlock"]
    post_search.register("second command")
    post_search.unregister("uxr post-search")
    assert post_search.commands() == ["second command"]

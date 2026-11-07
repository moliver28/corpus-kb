"""UXR hook behavior: detection, SessionStart lines, prompt nudges (#355).

A study is UXR when ``corpus.json`` has ``uxr.ticket``, or
``00-overview.md`` names a ticket, or a ``<PREFIX>-N.spec.json`` exists.
Hooks never run the analysis: they only read state and print lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import hook_events  # noqa: E402
import hook_session  # noqa: E402
import uxr_hooks  # noqa: E402
import uxr_store  # noqa: E402


def corpus_root(tmp_path: Path, monkeypatch, marker: dict | None = None) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    root = tmp_path / "study"
    (root / ".claude").mkdir(parents=True)
    payload = {"slug": "ux1", "watch": ["interviews"]}
    payload.update(marker or {})
    (root / ".claude" / "corpus.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (data / "studies.json").parent.mkdir(parents=True, exist_ok=True)
    (data / "studies.json").write_text(
        json.dumps(
            {
                "active": None,
                "studies": {payload["slug"]: {"database_url": "x", "port": 8010,
                                              "backend": "postgres"}},
            }
        ),
        encoding="utf-8",
    )
    (root / "interviews").mkdir()
    return root


def counts_stub(monkeypatch, documents=3) -> None:
    monkeypatch.setattr(
        hook_session,
        "fetch_counts",
        lambda study: {"documents": documents, "last_ingest": "2026-11-06T10:00:00"},
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detection_by_marker_uxr_ticket(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    assert uxr_hooks.is_uxr(root) == "UX1-275"


def test_detection_by_overview_ticket(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    (root / "00-overview.md").write_text("# Study UX1-9 study plan\n", encoding="utf-8")
    assert uxr_hooks.is_uxr(root) == "UX1-9"


def test_detection_by_spec_file_name(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    (root / "UX1-42.spec.json").write_text("{}", encoding="utf-8")
    assert uxr_hooks.is_uxr(root) == "UX1-42"


def test_detection_is_none_for_a_plain_study(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    assert uxr_hooks.is_uxr(root) is None


# ---------------------------------------------------------------------------
# SessionStart lines
# ---------------------------------------------------------------------------


def test_session_block_gains_uxr_lines_only_for_uxr_studies(
    tmp_path: Path, monkeypatch
) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    counts_stub(monkeypatch)
    payload = json.loads(hook_session.run_session_start({"cwd": str(root)}))
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert "UXR study UX1-275" in block
    assert "model output" in block
    assert "/corpus-uxr" in block


def test_plain_study_block_stays_unchanged(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    counts_stub(monkeypatch)
    payload = json.loads(hook_session.run_session_start({"cwd": str(root)}))
    block = payload["hookSpecificOutput"]["additionalContext"]
    assert "UXR" not in block
    assert "/corpus-uxr" not in block


# ---------------------------------------------------------------------------
# Prompt nudges
# ---------------------------------------------------------------------------


def test_phrase_injects_the_skill_path_once_per_session(
    tmp_path: Path, monkeypatch
) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    prompt = {"cwd": str(root), "session_id": "s1", "prompt": "is H1 supported?"}
    out = hook_events.run_prompt_submit(prompt)
    assert out is not None and "corpus-uxr" in out and "SKILL.md" in out
    again = hook_events.run_prompt_submit(prompt) or ""
    # The phrase fires once; a later prompt may only carry a lane nudge.
    assert "corpus-uxr" not in again


def test_queue_nudge_suggests_drain_once(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    queued = root / "interviews" / "p2.md"
    queued.write_text("**A**: x\n**B**: y\n**A**: z\n", encoding="utf-8")
    import ingest_queue

    ingest_queue.enqueue(root, queued)
    prompt = {"cwd": str(root), "session_id": "s2", "prompt": "hello there"}
    out = hook_events.run_prompt_submit(prompt)
    assert out is not None and "corpus-rag drain" in out
    assert hook_events.run_prompt_submit(prompt) is None


def test_analysis_nudge_when_sessions_have_no_rows(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    conn = uxr_store.connect("ux1")
    uxr_store.save_study(
        conn, spec_id="abc123", slug="ux1", ticket="UX1-275", questions=[]
    )
    uxr_store.upsert_source(conn, "p1", "interviews/p1.md", "participant_session")
    prompt = {"cwd": str(root), "session_id": "s3", "prompt": "hello there"}
    out = hook_events.run_prompt_submit(prompt)
    assert out is not None and "not analyzed" in out


def test_plain_study_prompt_gets_no_uxr_injection(
    tmp_path: Path, monkeypatch
) -> None:
    root = corpus_root(tmp_path, monkeypatch)
    prompt = {"cwd": str(root), "session_id": "s4", "prompt": "is H1 supported?"}
    out = hook_events.run_prompt_submit(prompt)
    # The plain ask-lane nudge may fire, but nothing UXR does.
    assert out is None or ("corpus-uxr" not in out and "UXR" not in out)


def test_hooks_never_create_or_write_a_store(tmp_path: Path, monkeypatch) -> None:
    root = corpus_root(tmp_path, monkeypatch, {"uxr": {"ticket": "UX1-275"}})
    hook_events.run_prompt_submit(
        {"cwd": str(root), "session_id": "s5", "prompt": "is H1 supported?"}
    )
    hook_session.run_session_start({"cwd": str(root)})
    stores = list((tmp_path / "plugin-data" / "uxr").glob("*.sqlite")) if (
        tmp_path / "plugin-data" / "uxr"
    ).is_dir() else []
    assert stores == []

"""Per-study SQLite storage for the UXR layer (issue #355).

State lives under ``${CLAUDE_PLUGIN_DATA}/uxr/<slug>.sqlite``. Tables:
``study``, ``question``, ``source``, ``session_insight``,
``schema_version``. No UXR code ever writes to a study database; this
file only opens its own SQLite stores.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_store  # noqa: E402


def open_store(tmp_path: Path, monkeypatch, slug: str = "ux1-study"):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    conn = uxr_store.connect(slug)
    uxr_store.save_study(
        conn,
        spec_id="0123456789abcdef",
        slug=slug,
        ticket="UX1-275",
        questions=[
            {"id": "RQ1", "kind": "rq", "position": 1, "label": "What breaks?"},
            {"id": "H1", "kind": "h", "position": 1, "label": "Export works"},
            {"id": "A1", "kind": "a", "position": 1, "label": "Nav holds"},
        ],
    )
    return conn


# ---------------------------------------------------------------------------
# Paths, schema, study and questions
# ---------------------------------------------------------------------------


def test_store_lives_under_plugin_data_uxr(tmp_path: Path, monkeypatch) -> None:
    open_store(tmp_path, monkeypatch)
    path = uxr_store.store_path("ux1-study")
    assert path == tmp_path / "plugin-data" / "uxr" / "ux1-study.sqlite"
    assert path.is_file()


def test_connect_is_idempotent_and_schema_version_is_pinned(
    tmp_path: Path, monkeypatch
) -> None:
    open_store(tmp_path, monkeypatch)
    again = uxr_store.connect("ux1-study")  # reopen: schema applies cleanly
    row = again.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == uxr_store.SCHEMA_VERSION


def test_study_roundtrip_and_questions(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    study = uxr_store.get_study(conn)
    assert study["ticket"] == "UX1-275"
    assert study["slug"] == "ux1-study"
    assert study["imported"] == 0
    ids = [q["id"] for q in uxr_store.questions(conn)]
    assert ids == ["RQ1", "H1", "A1"]


def test_reingest_the_same_spec_upserts_without_duplicating(
    tmp_path: Path, monkeypatch
) -> None:
    conn = open_store(tmp_path, monkeypatch)
    uxr_store.save_study(
        conn,
        spec_id="0123456789abcdef",
        slug="ux1-study",
        ticket="UX1-275",
        questions=[{"id": "RQ1", "kind": "rq", "position": 1, "label": "What breaks?"}],
    )
    assert len(uxr_store.questions(conn)) == 1
    assert uxr_store.get_study(conn)["id"] == "0123456789abcdef"


# ---------------------------------------------------------------------------
# Sources and rows
# ---------------------------------------------------------------------------


def test_sources_upsert_and_filter_by_class(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    uxr_store.upsert_source(conn, "p1", "interviews/p1.md", "participant_session")
    uxr_store.upsert_source(conn, "p1", "interviews/p1.md", "participant_session")
    uxr_store.upsert_source(conn, "team", "meetings/team.md", "internal_meeting")
    rows = uxr_store.sources(conn)
    assert len(rows) == 2
    only = uxr_store.sources(conn, classes=("participant_session",))
    assert [r["slug"] for r in only] == ["p1"]


def test_rows_save_and_filters(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    base = {
        "session": "p1",
        "unit_id": "H1",
        "stance": "Supports",
        "quote": "export worked",
        "quote_class": "exact",
        "rationale": "said it worked",
    }
    uxr_store.save_rows(conn, "0123456789abcdef", [dict(base, verified=True)])
    uxr_store.save_rows(
        conn,
        "0123456789abcdef",
        [
            dict(
                base,
                session="p2",
                stance="Challenges",
                verified=False,
                quote_class=None,
            )
        ],
    )
    assert len(uxr_store.rows(conn)) == 2
    only_h1 = uxr_store.rows(conn, question="H1")
    assert all(r["unit_id"] == "H1" for r in only_h1)
    only_p2 = uxr_store.rows(conn, session="p2")
    assert only_p2[0]["verified"] == 0
    by_stance = uxr_store.rows(conn, stance="Supports")
    assert [r["session"] for r in by_stance] == ["p1"]


def test_counts_and_sessions_with_rows(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    assert uxr_store.counts(conn) == {"verified": 0, "unverified": 0}
    assert uxr_store.sessions_with_rows(conn) == set()
    uxr_store.save_rows(
        conn,
        "0123456789abcdef",
        [
            {
                "session": "p1",
                "unit_id": "H1",
                "stance": "Supports",
                "quote": "q",
                "quote_class": "exact",
                "rationale": "r",
                "verified": True,
            }
        ],
    )
    assert uxr_store.counts(conn) == {"verified": 1, "unverified": 0}
    assert uxr_store.sessions_with_rows(conn) == {"p1"}


def test_imported_flag_roundtrip(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    assert uxr_store.imported(conn) is False
    uxr_store.set_imported(conn)
    assert uxr_store.imported(conn) is True

"""Per-study SQLite storage for the UXR layer (issue #355).

One SQLite file per study under ``${CLAUDE_PLUGIN_DATA}/uxr/``. The
engine study database is never written by UXR code; the only engine
reads go through the read-only ``corpus-rag sql`` route.

Tables: ``study``, ``question``, ``source``, ``session_insight``,
``schema_version``. Stdlib only.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from engine_paths import plugin_data_dir

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS study (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL, ticket TEXT NOT NULL,
  imported INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS question (
  id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES study(id),
  kind TEXT NOT NULL, position INTEGER NOT NULL, label TEXT
);
CREATE TABLE IF NOT EXISTS source (
  slug TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES study(id),
  path TEXT, class TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1,
  doc_id TEXT
);
CREATE TABLE IF NOT EXISTS session_insight (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id TEXT NOT NULL REFERENCES study(id),
  session TEXT NOT NULL, unit_id TEXT NOT NULL, stance TEXT NOT NULL,
  quote TEXT, quote_class TEXT, rationale TEXT,
  verified INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
"""

_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def slugify(raw: str) -> str:
    """One slug shape for store file names and source slugs."""
    slug = _SLUG_BAD.sub("-", raw.strip().lower()).strip("-")
    return slug or "study"


def store_dir() -> Path:
    return plugin_data_dir() / "uxr"


def store_path(slug: str) -> Path:
    return store_dir() / f"{slugify(slug)}.sqlite"


def connect(slug: str) -> sqlite3.Connection:
    """Open (and create) the study's store with the schema applied."""
    path = store_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO schema_version (version) SELECT ? WHERE NOT EXISTS "
        "(SELECT 1 FROM schema_version)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def connect_existing(slug: str) -> sqlite3.Connection | None:
    """Open the store read-only when it exists; None when absent.

    Hooks use this: a hook never creates or writes a store.
    """
    path = store_path(slug)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Study and questions
# ---------------------------------------------------------------------------


def save_study(
    conn: sqlite3.Connection,
    spec_id: str,
    slug: str,
    ticket: str,
    questions: list[dict],
) -> None:
    """Upsert the study; the spec's question set replaces the stored one."""
    now = time.time()
    conn.execute(
        "INSERT INTO study (id, slug, ticket, imported, created_at) "
        "VALUES (?, ?, ?, 0, ?) "
        "ON CONFLICT(id) DO UPDATE SET slug = excluded.slug, "
        "ticket = excluded.ticket",
        (spec_id, slug, ticket, now),
    )
    # The spec is the source of truth: re-ingesting one replaces its
    # question set wholesale.
    conn.execute("DELETE FROM question WHERE study_id = ?", (spec_id,))
    for q in questions:
        conn.execute(
            "INSERT INTO question (id, study_id, kind, position, label) VALUES (?, ?, ?, ?, ?)",
            (q["id"], spec_id, q["kind"], int(q["position"]), q.get("label")),
        )
    conn.commit()


def get_study(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM study LIMIT 1").fetchone()
    return dict(row) if row is not None else None


def questions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT q.* FROM question q JOIN study s ON q.study_id = s.id "
        "ORDER BY CASE q.kind WHEN 'rq' THEN 0 WHEN 'h' THEN 1 ELSE 2 END, "
        "q.position, q.id"
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def upsert_source(
    conn: sqlite3.Connection,
    slug: str,
    path: str,
    class_: str,
    confirmed: bool = True,
    doc_id: str | None = None,
) -> None:
    study = get_study(conn)
    if study is None:
        raise ValueError("no study stored; run ingest-plan first")
    conn.execute(
        "INSERT INTO source (slug, study_id, path, class, confirmed, doc_id) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET path = excluded.path, "
        "class = excluded.class, confirmed = excluded.confirmed",
        (slug, study["id"], path, class_, int(confirmed), doc_id),
    )
    conn.commit()


def sources(conn: sqlite3.Connection, classes: tuple[str, ...] | None = None) -> list[dict]:
    if classes:
        marks = ",".join("?" for _ in classes)
        rows = conn.execute(
            f"SELECT * FROM source WHERE class IN ({marks}) ORDER BY slug",
            classes,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM source ORDER BY slug").fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def save_rows(conn: sqlite3.Connection, study_id: str, rows: list[dict]) -> None:
    """Store validated rows; ``verified`` and ``quote_class`` come along."""
    now = time.time()
    for row in rows:
        conn.execute(
            "INSERT INTO session_insight (study_id, session, unit_id, stance, "
            "quote, quote_class, rationale, verified, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                study_id,
                str(row.get("session") or ""),
                str(row.get("unit_id") or ""),
                str(row.get("stance") or ""),
                row.get("quote"),
                row.get("quote_class"),
                row.get("rationale"),
                1 if row.get("verified") else 0,
                now,
            ),
        )
    conn.commit()


def rows(
    conn: sqlite3.Connection,
    question: str | None = None,
    stance: str | None = None,
    session: str | None = None,
) -> list[dict]:
    sql = (
        "SELECT si.*, src.path AS source_path FROM session_insight si "
        "LEFT JOIN source src ON src.slug = si.session WHERE 1=1"
    )
    params: list[str] = []
    for column, value in (
        ("si.unit_id", question),
        ("si.stance", stance),
        ("si.session", session),
    ):
        if value:
            sql += f" AND {column} = ?"
            params.append(value)
    sql += " ORDER BY si.id"
    return [dict(row) for row in conn.execute(sql, params)]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT SUM(verified) AS v, SUM(1 - verified) AS u FROM session_insight"
    ).fetchone()
    return {"verified": int(row["v"] or 0), "unverified": int(row["u"] or 0)}


def sessions_with_rows(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT DISTINCT session FROM session_insight").fetchall()
    return {str(row["session"]) for row in rows}


def confirmed_sessions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT slug FROM source WHERE confirmed = 1 AND "
        "class IN ('participant_session', 'internal_meeting')"
    ).fetchall()
    return {str(row["slug"]) for row in rows}


# ---------------------------------------------------------------------------
# Import flag
# ---------------------------------------------------------------------------


def imported(conn: sqlite3.Connection) -> bool:
    study = get_study(conn)
    return bool(study and study["imported"])


def set_imported(conn: sqlite3.Connection) -> None:
    study = get_study(conn)
    if study is None:
        return
    conn.execute("UPDATE study SET imported = 1 WHERE id = ?", (study["id"],))
    conn.commit()

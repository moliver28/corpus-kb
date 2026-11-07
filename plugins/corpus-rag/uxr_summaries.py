"""The ``uxr`` output handlers: summaries, insights, import, post-search.

``apply-summary`` writes a marked block per session and is byte-stable
on a second run. ``insights`` shows stored rows with checked quotes and
the coverage counts. ``import`` copies 0.6.0 rows once, read-only on the
engine side. ``post-search`` is the internal command the ``post_search``
extension point runs after a search.

Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import uxr_commands
import uxr_files
import uxr_rollup
import uxr_store
from uxr_common import root_of, slug_for

BLOCK_LIMIT = 3
HEADER = "Derived insights (model output, not evidence):"


# ---------------------------------------------------------------------------
# write-themes, apply-summary
# ---------------------------------------------------------------------------


def cmd_uxr_write_themes(args) -> int:
    """Write the narrative between the markers in analysis/themes.md."""
    try:
        text = Path(args.narrative_file).read_text(encoding="utf-8")
    except OSError as exc:
        from uxr_common import UxrError

        raise UxrError(f"cannot read the narrative file: {exc}") from exc
    target = uxr_files.write_themes(root_of(args), text)
    print(f"ok: themes written to {target.name} between the corpus markers.")
    print("analysis/codes.md is never opened by this command.")
    return 0


def _session_blocks(conn) -> dict[str, list[str]]:
    """Per-session unit lines from verified rows: 'H1: Supports 2, Mixed 1'."""
    blocks: dict[str, dict[str, dict[str, int]]] = {}
    for row in uxr_store.rows(conn):
        if not row.get("verified"):
            continue
        session = str(row.get("session") or "")
        unit = str(row.get("unit_id") or "")
        stance = str(row.get("stance") or "")
        blocks.setdefault(session, {}).setdefault(unit, {})
        blocks[session][unit][stance] = blocks[session][unit].get(stance, 0) + 1
    out: dict[str, list[str]] = {}
    for session, units in blocks.items():
        unit_lines = {
            unit: ", ".join(
                f"{stance} {count}" if count > 1 else stance for stance, count in stances.items()
            )
            for unit, stances in units.items()
        }
        out[session] = uxr_files.summary_block_text(unit_lines)
    return out


def cmd_uxr_apply_summary(args) -> int:
    """Write a marked block into each session summary; researcher wins."""
    conn = uxr_store.connect(slug_for(args))
    actions = uxr_files.apply_summaries(root_of(args), _session_blocks(conn))
    if not actions:
        print("no analyzed sessions to summarize.")
        return 0
    for action in actions:
        print(action)
    return 0


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------


def cmd_uxr_insights(args) -> int:
    """Stored rows with quotes, sources, rollup, and coverage counts."""
    conn = uxr_store.connect(slug_for(args))
    rows = uxr_store.rows(conn, question=args.question, stance=args.stance, session=args.session)
    study = uxr_store.get_study(conn)
    ingested = uxr_commands.engine_document_count(args.study_slug)
    analyzed = len(uxr_store.sessions_with_rows(conn))
    report = uxr_rollup.build(rows, {})
    if args.format == "json":
        print(json.dumps(_insights_json(rows, report, ingested, analyzed)))
        return 0
    ticket = study["ticket"] if study else "?"
    print(f"Insights for '{args.study_slug or 'default'}' (ticket {ticket}):")
    _print_rows(rows)
    print("Rollup:")
    import uxr_analysis

    uxr_analysis.print_rollup(report)
    print(
        f"Ingested: {ingested if ingested is not None else 'n/a'} documents; "
        f"analyzed: {analyzed} session(s)."
    )
    return 0


def _print_rows(rows: list[dict]) -> None:
    for row in rows:
        print(f"{row['unit_id']}: {row['stance']} [S:{row['session']}]")
        if row.get("quote"):
            print(f'    quote ({row.get("quote_class") or "unchecked"}): "{row["quote"]}"')
        if row.get("rationale"):
            print(f"    rationale: {row['rationale']}")
        if not row.get("verified"):
            print("    unverified row; re-check before use.")


def _insights_json(rows: list[dict], report: dict, ingested: int | None, analyzed: int) -> dict:
    return {
        "rows": [
            {
                "unit_id": row["unit_id"],
                "stance": row["stance"],
                "source": row["session"],
                "quote": row.get("quote") or "",
                "quote_class": row.get("quote_class"),
                "rationale": row.get("rationale") or "",
                "verified": bool(row.get("verified")),
            }
            for row in rows
        ],
        "rollup": report,
        "ingested": ingested,
        "analyzed": analyzed,
    }


# ---------------------------------------------------------------------------
# import, post-search
# ---------------------------------------------------------------------------


def cmd_uxr_import(args) -> int:
    """One-time copy from a 0.6.0 ``uxr`` schema; read-only on the engine."""
    conn = uxr_store.connect(slug_for(args))
    if uxr_store.imported(conn):
        print("already imported; nothing to do.")
        return 0
    legacy = uxr_commands.fetch_legacy_rows(args.study_slug)
    if legacy is None:
        print("no legacy uxr schema found; nothing to import.")
        return 0
    payload = [
        {
            "session": row.get("session"),
            "unit_id": row.get("unit_id"),
            "stance": row.get("stance"),
            "quote": row.get("quote"),
            "quote_class": row.get("quote_class") or "exact",
            "rationale": row.get("rationale"),
            "verified": True,
        }
        for row in legacy
    ]
    study = uxr_store.get_study(conn)
    uxr_store.save_rows(conn, study["id"] if study else "", payload)
    uxr_store.set_imported(conn)
    print(f"ok: imported {len(payload)} row(s) from the 0.6.0 uxr schema.")
    return 0


def post_search_block(slug: str, _query: str) -> str | None:
    """Up to three stored rows for the search extension point."""
    conn = uxr_store.connect_existing(slug)
    if conn is None:
        return None
    rows = uxr_store.rows(conn)
    if not rows:
        return None
    rows.sort(
        key=lambda row: (
            0 if row.get("verified") else 1,
            uxr_rollup._unit_sort_key(str(row.get("unit_id") or "")),
            int(row.get("id") or 0),
        )
    )
    lines = [HEADER]
    for row in rows[:BLOCK_LIMIT]:
        line = f"  {row['unit_id']}: {row['stance']} [S:{row['session']}]"
        if row.get("quote"):
            line += f' "{row["quote"]}"'
        lines.append(line)
    return "\n".join(lines)


def cmd_uxr_post_search(args) -> int:
    """The internal post_search command; never prints in a plain study."""
    try:
        payload = json.loads(args.post_search_json or "{}")
    except ValueError:
        payload = {}
    slug = uxr_store.slugify(str(payload.get("slug") or "default"))
    block = post_search_block(slug, str(payload.get("query") or ""))
    if block:
        print(block)
    return 0

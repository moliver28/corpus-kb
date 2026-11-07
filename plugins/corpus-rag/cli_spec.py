"""The ``corpus-rag`` argument parser and command map (issue #351).

``bin/corpus-rag`` stays thin: build the parser, dispatch to one command
body. Every per-study command takes ``-p <slug>``; the 4-step selection
lives in ``study_registry.resolve_study``.
"""

from __future__ import annotations

import argparse

import study_admin
import study_commands
import study_maintenance
import study_queue
import study_query


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus-rag", description="Corpus-KB plugin helper with study registry."
    )
    subs = parser.add_subparsers(dest="command", required=True)

    status = subs.add_parser("status", help="Per-study status; doctor decides the verdict.")
    status.add_argument("-p", "--study-slug", default=None, help="The study slug")

    subs.add_parser("connect", help="Print the MCP config JSON: one entry per study.")

    prov = subs.add_parser(
        "provision", help="Create, migrate, and register one study."
    )
    prov.add_argument("slug", help="The study slug, like interview-study")
    prov.add_argument(
        "--backend", default=None, help="postgres or duckdb (default: the userConfig value)"
    )
    prov.add_argument(
        "--project-id",
        default=None,
        help="0.7.x compatibility: routes to raw-provision (Track C raw tables)",
    )

    raw = subs.add_parser(
        "raw-provision", help="Create the Track C raw table for one project slug."
    )
    raw.add_argument("slug", help="The project slug, like meeting-notes")
    raw.add_argument("--project-id", default=None, help="Research project id")

    subs.add_parser("list", help="Studies with database, port, up or down, active mark.")

    use = subs.add_parser("use", help="Set the active study.")
    use.add_argument("slug", help="A study slug from corpus-rag list")

    serve = subs.add_parser("serve", help="Start the study's server.")
    serve.add_argument("-p", "--study-slug", default=None, help="The study slug")

    stop = subs.add_parser("stop", help="Stop the study's server.")
    stop.add_argument("-p", "--study-slug", default=None, help="The study slug")

    ingest = subs.add_parser("ingest", help="Ingest one file or folder into the study.")
    ingest.add_argument("path", help="The file or folder to ingest")
    ingest.add_argument("-p", "--study-slug", default=None, help="The study slug")

    search = subs.add_parser("search", help="Hybrid or context search in the study.")
    search.add_argument("query", help="The search text")
    search.add_argument("-p", "--study-slug", default=None, help="The study slug")
    search.add_argument("-k", type=int, default=5, help="How many hits (default 5)")
    search.add_argument(
        "--mode", choices=("hybrid", "context"), default="hybrid", help="Search mode"
    )
    search.add_argument(
        "--context-chunks", type=int, default=2, help="Context chunks per hit (context mode)"
    )

    similar = subs.add_parser("similar", help="Chunks nearest to one stored chunk.")
    similar.add_argument("chunk_id", help="The stored chunk id")
    similar.add_argument("-p", "--study-slug", default=None, help="The study slug")
    similar.add_argument("-k", type=int, default=5, help="How many hits (default 5)")

    ask = subs.add_parser("ask", help="A cited answer from the study.")
    ask.add_argument("question", help="The question text")
    ask.add_argument("-p", "--study-slug", default=None, help="The study slug")

    sql = subs.add_parser("sql", help="Read-only SQL against the study database.")
    sql.add_argument("query", help="A SELECT statement")
    sql.add_argument("-p", "--study-slug", default=None, help="The study slug")

    events = subs.add_parser("events", help="Event-store health for the study.")
    events.add_argument("-p", "--study-slug", default=None, help="The study slug")

    doctor = subs.add_parser("doctor", help="Engine diagnostics with the study's database.")
    doctor.add_argument("-p", "--study-slug", default=None, help="The study slug")

    migrate = subs.add_parser("migrate", help="Apply pending migrations per study.")
    migrate.add_argument("study_slug", nargs="?", default=None, help="A study slug")
    migrate.add_argument("--all", action="store_true", help="Every registered study")
    migrate.add_argument(
        "--reset-embeddings", action="store_true", help="Accept that heavy migrations drop vectors"
    )
    migrate.add_argument("--superuser-dsn", default=None, help="Override the userConfig DSN")

    reembed = subs.add_parser("reembed", help="Rebuild missing vectors for the study.")
    reembed.add_argument("-p", "--study-slug", default=None, help="The study slug")
    reembed.add_argument("--limit", type=int, default=None, help="Cap on chunks re-embedded")

    backup = subs.add_parser("backup", help="Back one study up (pg_dump or file copy).")
    backup.add_argument("study_slug", help="The study slug")

    ops = subs.add_parser("ops", help="The live op inventory.")
    ops.add_argument("--family", default=None, help="Only this family")

    drain = subs.add_parser(
        "drain", help="Send the study folder's pending ingest queue."
    )
    drain.add_argument("-p", "--study-slug", default=None, help="The study slug")
    drain.add_argument(
        "--approve",
        action="store_true",
        help="Drain even when auto_ingest is off for this study",
    )

    op = subs.add_parser("op", help="Run any op against the study server.")
    op.add_argument("name", help="The op name from corpus-rag ops")
    op.add_argument("pairs", nargs="*", default=[], help="key=value arguments")
    op.add_argument("--json", default=None, help="A JSON object merged under the pairs")
    op.add_argument("-p", "--study-slug", default=None, help="The study slug")

    engine = subs.add_parser("engine", help="Manage the engine install in the state folder.")
    engine_subs = engine.add_subparsers(dest="engine_command", required=True)
    inst = engine_subs.add_parser(
        "install",
        help="Clone the engine at tag corpus-rag-v<version> and sync it with uv.",
    )
    inst.add_argument(
        "--force", action="store_true", help="Reinstall, even when this version is already present."
    )
    return parser


HANDLERS = {
    "status": study_commands.cmd_status,
    "connect": study_commands.cmd_connect,
    "provision": study_admin.cmd_provision,
    "raw-provision": study_admin.cmd_raw_provision,
    "list": study_commands.cmd_list,
    "use": study_commands.cmd_use,
    "serve": study_commands.cmd_serve,
    "stop": study_commands.cmd_stop,
    "ingest": study_query.cmd_ingest,
    "search": study_query.cmd_search,
    "similar": study_query.cmd_similar,
    "ask": study_query.cmd_ask,
    "sql": study_query.cmd_sql,
    "events": study_query.cmd_events,
    "doctor": study_admin.cmd_doctor,
    "migrate": study_admin.cmd_migrate,
    "reembed": study_admin.cmd_reembed,
    "backup": study_maintenance.cmd_backup,
    "ops": study_maintenance.cmd_ops,
    "op": study_maintenance.cmd_op,
    "drain": study_queue.cmd_drain,
}

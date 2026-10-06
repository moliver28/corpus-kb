"""Corpus-KB user-facing CLI.

Wires the setup/doctor diagnostics and server start commands behind a single
``corpus-kb`` entry point.
"""

from __future__ import annotations

import typer

app = typer.Typer(help="Corpus-KB command-line interface")


@app.command()
def setup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print steps without executing"),
    fresh: bool = typer.Option(False, "--fresh", help="Reset checkpoint and start from phase 1"),
    build_local: bool = typer.Option(
        False,
        "--build-local",
        help=(
            "Build the Postgres image from docker/postgres/Dockerfile "
            "instead of using the pre-built image"
        ),
    ),
    skip_image_verify: bool = typer.Option(
        False,
        "--skip-image-verify",
        help="Skip cosign verification of the Postgres container image",
    ),
) -> int:
    """One-line docker-compose + database + migrations + models setup."""
    from corpus_kb._setup.install import main as install_main

    argv = ["setup"]
    if dry_run:
        argv.append("--dry-run")
    if fresh:
        argv.append("--fresh")
    if build_local:
        argv.append("--build-local")
    if skip_image_verify:
        argv.append("--skip-image-verify")
    return install_main(argv)


@app.command()
def doctor() -> int:
    """Read-only system diagnostics."""
    from corpus_kb._setup.install import main as install_main

    return install_main(["doctor"])


@app.command()
def start(
    transport: str = typer.Option("http", "--transport", help="Server transport"),
    port: int = typer.Option(8010, "--port", help="HTTP server port"),
) -> int:
    """Start the Corpus-KB server."""
    from corpus_kb.server_wiring import main as server_main

    server_main(["--transport", transport, "--port", str(port)])
    return 0


research_app = typer.Typer(help="Research domain: transcript ingestion")
app.add_typer(research_app, name="research")


def _ingest_coroutine(path: str, project_id: str | None, watch: bool, force: bool):

    from corpus_kb.config import load_config
    from corpus_kb.handlers.research_handler import get_research_handler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder

    async def _run() -> int:
        import asyncpg

        cfg = load_config()
        db = cfg.get("database", {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            app = _get_app(conn_str)
            reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
            handler = get_research_handler(pool)
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            checkpoint = CheckpointManager(pool)
            dlq = DLQHandler(pool)
            projection = ResearchProjection(pool, checkpoint, dlq, embedder)
            docs = DocumentsProjection(pool, checkpoint, dlq)
            embeds = EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq)
            if watch:
                from pathlib import Path

                from corpus_kb.research.dynamic_ingest import watch as watch_dir

                research_cfg = cfg.get("research", {}) or {}
                ingest_cfg = research_cfg.get("ingest", {}) or {}
                interval = int(ingest_cfg.get("watch_interval_s", 10))

                async def ingest_one(file_path: Path) -> None:
                    await handler.ingest_transcript(_tenant(), str(file_path), _pid(project_id))
                    await projection.catch_up(reader)
                    await docs.catch_up(reader, _tenant())
                    await embeds.catch_up(reader, _tenant())

                await watch_dir(pool, _tenant(), Path(path), interval, ingest_one)
                return 0
            result = await handler.ingest_path(_tenant(), path, _pid(project_id), force=force)
            await projection.catch_up(reader)
            await docs.catch_up(reader, _tenant())
            await embeds.catch_up(reader, _tenant())
            import json

            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("status") == "success" else 1
        finally:
            await pool.close()

    return _run()


_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _tenant():
    from uuid import UUID

    return UUID(_DEFAULT_TENANT)


def _pid(project_id: str | None):
    from uuid import UUID

    return UUID(project_id) if project_id else None


def _get_app(conn_str: str):
    from corpus_kb.domain.application import get_app

    return get_app(conn_str)


@research_app.command("ingest-transcript")
def research_ingest_transcript(
    path: str = typer.Argument(..., help="Transcript file (txt|vtt|srt|csv|docx)"),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if file hash unchanged"),
) -> int:
    """Ingest one transcript: turns -> roles -> exchanges -> event stream."""

    import asyncio

    return asyncio.run(_ingest_coroutine(path, project_id, watch=False, force=force))


@research_app.command("ingest")
def research_ingest(
    path: str = typer.Argument(..., help="File, directory, or glob"),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    watch: bool = typer.Option(False, "--watch", help="Tail a drop directory forever"),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if file hash unchanged"),
) -> int:
    """Dynamic ingestion with separate file-hash and text-hash dedup."""
    import asyncio

    return asyncio.run(_ingest_coroutine(path, project_id, watch=watch, force=force))

"""Corpus-KB user-facing CLI.

Wires the setup/doctor diagnostics and server start commands behind a single
``corpus-kb`` entry point.
"""

from __future__ import annotations

from typing import cast

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

coding_app = typer.Typer(help="Coding subsystem: deductive coding runs")
app.add_typer(coding_app, name="coding")

codebook_app = typer.Typer(help="Codebook governance: human-gated promotion")
app.add_typer(codebook_app, name="codebook")

review_app = typer.Typer(help="Review queue: execute accept/override decisions")
app.add_typer(review_app, name="review")


def _review_command(decision: str):
    def _cmd(
        assignment_id: str = typer.Argument(..., help="CodingAssignment aggregate UUID"),
        reviewer: str = typer.Option(..., "--reviewer", help="Reviewer identity"),
        note: str = typer.Option(
            "", "--note", help="Decision note (link fixes describe the change)"
        ),
    ) -> int:
        """Record one reviewer decision and advance the projections."""
        import asyncio
        import json

        from corpus_kb.config import load_config

        async def _run() -> int:
            from uuid import UUID

            import asyncpg

            cfg = load_config()
            db = cfg.get("database", {})
            conn_str = str(db.get("connection_string", ""))
            pool = await asyncpg.create_pool(conn_str)
            try:
                from corpus_kb.research.review_surface import execute_review

                result = await execute_review(
                    pool,
                    _tenant(),
                    conn_str,
                    UUID(assignment_id),
                    reviewer,
                    decision,
                    note,
                )
                print(json.dumps(result, indent=2, default=str))
                return 0
            finally:
                await pool.close()

        return asyncio.run(_run())

    return _cmd


review_app.command("accept", help="Confirm the model's assignment")(_review_command("accept"))
review_app.command("override", help="Overrule the model's assignment")(_review_command("override"))


@coding_app.command("run")
def coding_run(
    codebook_version_id: str = typer.Option(
        ..., "--codebook-version-id", help="Codebook version UUID to code against"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one research project"),
    alpha: float = typer.Option(
        0.1, "--alpha", help="Conformal miscoverage level (1-alpha coverage)"
    ),
) -> int:
    """Deductive coding run (v5 §8): three-view scoring + calibrated thresholds."""
    import asyncio

    return asyncio.run(_coding_run_coroutine(codebook_version_id, project_id, alpha))


def _coding_run_coroutine(codebook_version_id: str, project_id: str | None, alpha: float):
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
    from corpus_kb.research.deductive_run import run_deductive

    async def _run() -> int:
        import asyncpg

        cfg = load_config()
        db = cfg.get("database", {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            app = _get_app(conn_str)
            reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
            get_research_handler(pool)
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            checkpoint = CheckpointManager(pool)
            dlq = DLQHandler(pool)
            projection = ResearchProjection(pool, checkpoint, dlq, embedder)
            docs = DocumentsProjection(pool, checkpoint, dlq)
            embeds = EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq)

            from uuid import UUID as _UUID

            summary = await run_deductive(
                pool,
                _tenant(),
                _UUID(codebook_version_id),
                project_id=_pid(project_id),
                cal_alpha=alpha,
            )
            await projection.catch_up(reader)
            await docs.catch_up(reader, _tenant())
            await embeds.catch_up(reader, _tenant())
            import json

            print(json.dumps(summary, indent=2, default=str))
            return 0 if summary.get("status") == "success" else 1
        finally:
            await pool.close()

    return _run()


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


@coding_app.command("inductive")
def coding_inductive(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
) -> int:
    """Inductive pass (v5 §9): summaries -> UMAP/HDBSCAN -> proposed codes."""
    import asyncio

    return asyncio.run(_inductive_coroutine(project_id))


def _inductive_coroutine(project_id: str | None):
    from corpus_kb.config import load_config
    from corpus_kb.handlers.llm_handler import LlmHandler
    from corpus_kb.handlers.research_handler import get_research_handler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder
    from corpus_kb.research.inductive_run import run_inductive

    async def _run() -> int:
        import json

        import asyncpg

        cfg = load_config()
        db = cfg.get("database", {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            app = _get_app(conn_str)
            reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
            get_research_handler(pool)
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            checkpoint = CheckpointManager(pool)
            dlq = DLQHandler(pool)
            projection = ResearchProjection(pool, checkpoint, dlq, embedder)
            docs = DocumentsProjection(pool, checkpoint, dlq)
            embeds = EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq)

            summary = await run_inductive(
                pool,
                _tenant(),
                project_id=_pid(project_id),
                llm=LlmHandler(cfg),
                embedder=embedder,
                cfg=cfg,
            )
            await projection.catch_up(reader)
            await docs.catch_up(reader, _tenant())
            await embeds.catch_up(reader, _tenant())
            print(json.dumps(summary, indent=2, default=str))
            if summary.get("status") == "unavailable":
                return 2
            return 0 if summary.get("status") == "success" else 1
        finally:
            await pool.close()

    return _run()


@codebook_app.command("promote")
def codebook_promote(
    proposed_id: int = typer.Option(..., "--proposed-id", help="research_proposed_codes id"),
    name: str = typer.Option(..., "--name", help="Human-approved code name"),
    definition: str = typer.Option(..., "--definition", help="Human-approved definition"),
    inclusion: str = typer.Option("", "--inclusion", help="Inclusion criteria"),
    exclusion: str = typer.Option("", "--exclusion", help="Exclusion criteria"),
    label: str = typer.Option(None, "--label", help="Version label hint (default: cluster terms)"),
    tau_dup: float = typer.Option(
        None, "--tau-dup", help="Duplicate-gate threshold (default: calibrated/config)"
    ),
) -> int:
    """Promote one proposed code into a NEW codebook version (human gate)."""
    import asyncio

    return asyncio.run(
        _promote_coroutine(proposed_id, name, definition, inclusion, exclusion, label, tau_dup)
    )


def _promote_coroutine(
    proposed_id: int,
    name: str,
    definition: str,
    inclusion: str,
    exclusion: str,
    label: str | None,
    tau_dup: float | None,
):
    from corpus_kb.config import load_config
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder
    from corpus_kb.research.inductive_run import inductive_config
    from corpus_kb.research.promote_code import promote_proposal

    async def _run() -> int:
        import json

        import asyncpg

        cfg = load_config()
        db = cfg.get("database", {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            app = _get_app(conn_str)
            reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            checkpoint = CheckpointManager(pool)
            dlq = DLQHandler(pool)
            projection = ResearchProjection(pool, checkpoint, dlq, embedder)
            docs = DocumentsProjection(pool, checkpoint, dlq)
            embeds = EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq)

            settings = inductive_config(cfg)
            effective_tau = tau_dup if tau_dup is not None else float(str(settings["tau_dup"]))
            result = await promote_proposal(
                pool,
                _tenant(),
                proposed_id,
                name,
                definition,
                inclusion=inclusion,
                exclusion=exclusion,
                tau_dup=effective_tau,
                label=label,
            )
            await projection.catch_up(reader)
            await docs.catch_up(reader, _tenant())
            await embeds.catch_up(reader, _tenant())
            print(json.dumps(result, indent=2, default=str))
            if result.get("status") == "promoted":
                return 0
            if result.get("status") == "duplicate_blocked":
                return 3
            return 1
        finally:
            await pool.close()

    return _run()


@research_app.command("report")
def research_report(
    codebook_version_id: str = typer.Option(
        None, "--codebook-version", help="Version (default: latest)"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    level: str = typer.Option(
        "novice", "--level", help="Presentation level: novice (traffic lights + guidance) or expert"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable full schema"),
) -> int:
    """Emit the governance report (v5 §11/§13/§14 in ONE artifact)."""
    import asyncio

    return asyncio.run(_report_coroutine(codebook_version_id, project_id, level, json_output))


def _report_coroutine(
    codebook_version_id: str | None,
    project_id: str | None,
    level: str,
    json_output: bool,
):
    import json

    import asyncpg

    from corpus_kb.config import load_config
    from corpus_kb.research.governance_report import to_dict, validate_required
    from corpus_kb.research.report_runner import build_report

    async def _run() -> int:
        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            report = await build_report(
                pool,
                _tenant(),
                _pid(codebook_version_id),
                level=level,
                run_manifest={"project_id": str(project_id) if project_id else None},
            )
            payload = to_dict(report)
            missing = validate_required(payload)
            if missing:
                print(f"report missing required fields: {missing}")
                return 2
            if json_output:
                print(json.dumps(payload, indent=2, default=str))
            else:
                _print_report(report, level)
            return 0
        finally:
            await pool.close()

    return _run()


def _print_report(report: object, level: str) -> None:
    from corpus_kb.research import guide_copy
    from corpus_kb.research.governance_report import (
        ResearchReport,
        _mapping,
        novice_view,
        to_dict,
    )

    print(guide_copy.REPORT_TITLE)
    print(guide_copy.REPORT_SUBTITLE)
    is_report = isinstance(report, ResearchReport)
    payload = to_dict(report) if is_report else cast_dict(report)
    if level == guide_copy.LEVEL_NOVICE and is_report:
        stored = payload.get("novice_view")
        view = _mapping(stored) if stored else novice_view(report)
        actions = _mapping(view).get("next_actions", [])
        for _name, light in _mapping(_mapping(view).get("traffic_lights")).items():
            level_text = str(_mapping(light).get("level", ""))
            message = str(_mapping(light).get("message", ""))
            print(f"[{level_text.upper()}] {message}")
        print()
        for _name, text in _mapping(_mapping(view).get("plain_language")).items():
            print(f"- {text}")
        print()
        print(guide_copy.NEXT_ACTIONS_HEADER)
        for action in cast_list(actions):
            print(f"-> {action}")
    else:
        for key in (
            "isr_pooled",
            "coverage_explicit",
            "coverage_qdep",
            "gray_zone_share",
            "tier3_disagreement_rate",
        ):
            print(f"{key}: {payload.get(key)}")
        for key in (
            "residual",
            "coverage_curve",
            "missing_codes",
            "cluster_stability",
            "overlap",
            "keywords",
            "kappa_alpha",
            "g3_audit",
            "conformal",
            "codebook_diff",
            "run_manifest",
        ):
            print(f"{key}: {_mapping(payload.get(key)) or payload.get(key)}")


def cast_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        return payload
    return {}


def cast_list(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    return []

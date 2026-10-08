"""Corpus-KB user-facing CLI.

Wires the setup/doctor diagnostics and server start commands behind a single
``corpus-kb`` entry point.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import typer

app = typer.Typer(help="Corpus-KB command-line interface")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    transport: str = typer.Option(
        None,
        "--transport",
        help="Reserved for the MCP stdio editor surface (not yet implemented)",
    ),
) -> None:
    """Corpus-KB: local RAG knowledge base for AI code editors."""
    if transport is not None:
        # The mcp-configs/ editor configs launch `corpus-kb --transport
        # stdio`. The FastMCP stdio server is not wired yet; failing with a
        # clear message beats typer's "no such option" crash, and running
        # the HTTP server on stdio would speak garbage to the editor.
        print(
            "corpus-kb does not yet speak MCP over stdio; the editor-facing "
            "API today is HTTP (corpus-kb start --transport http --port 8010)."
        )
        raise typer.Exit(code=2)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


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


async def _cli_approve(stage: str) -> bool:
    """The on-mode CLI approval boundary: one blocking y/N prompt.

    ASYNC by contract: cycle_render.approval awaits its ``approve`` argument,
    so a sync prompt here crashed with "object bool can't be used in 'await'
    expression" on BOTH approve and deny (review F-2).
    """
    from corpus_kb.research import guide_copy

    print(guide_copy.CYCLE_APPROVAL_STAGE_HEADER.format(stage=stage))
    try:
        reply = input(guide_copy.CYCLE_APPROVAL_PROMPT)
    except (EOFError, OSError):
        # Closed stdin (scripts, CI, detached editors) must not crash the
        # boundary with a traceback: no answer is a denial, same as "n".
        print(guide_copy.CYCLE_APPROVAL_NO_STDIN)
        return False
    return reply.strip().lower() in ("y", "yes")


def _cli_error_exit(exc: BaseException) -> int | None:
    """Map an EXPECTED CLI failure to an exit code; None means re-raise.

    Covers the error classes a user can cause from the keyboard (unknown
    gate/proposal id, bad UUID, permission denied, system libpq too old);
    unexpected bugs keep their traceback (review F-8).
    """
    import asyncpg
    import psycopg

    from corpus_kb.research import guide_copy

    if isinstance(exc, ValueError):
        # unknown gate name, unknown proposal id, malformed UUID, bad decision
        print(f"ERROR: {exc}")
        return 1
    if isinstance(exc, asyncpg.PostgresError):
        denied = getattr(exc, "sqlstate", "") == "42501" or "permission denied" in str(exc)
        if denied:
            print(f"ERROR: Postgres permission denied: {exc}")
            print(guide_copy.DB_PERMISSION_DENIED_HINT)
        else:
            print(f"ERROR: Postgres error: {exc}")
        return 1
    if isinstance(exc, psycopg.Error):
        if isinstance(exc, psycopg.NotSupportedError):
            print(f"ERROR: {exc}")
            print(guide_copy.LIBPQ_TOO_OLD_HINT)
        elif getattr(exc, "sqlstate", "") == "42501":
            print(f"ERROR: Postgres permission denied: {exc}")
            print(guide_copy.DB_PERMISSION_DENIED_HINT)
        else:
            print(f"ERROR: database error: {exc}")
        return 1
    return None


def _run_async(coro: object) -> int:
    """asyncio.run with expected-error presentation and honest exit codes.

    typer ignores a plain int return on failure paths, so BOTH failure shapes
    must raise: mapped expected errors (clean message + hint) and non-zero
    status codes from the coroutine (e.g. 2 = stage unavailable) exit with
    their real code instead of a silent 0.
    """
    import asyncio
    from collections.abc import Coroutine
    from typing import Any, cast

    try:
        code = asyncio.run(cast("Coroutine[Any, Any, int]", coro))
    except Exception as exc:
        mapped = _cli_error_exit(exc)
        if mapped is None:
            raise
        raise typer.Exit(code=mapped) from None
    if code:
        raise typer.Exit(code=code)
    return code


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

# Module-level singleton (B008): mutable-annotated typer defaults must not
# call typer.Option inline.
_DOC_ID_OPTION = typer.Option(None, "--doc-id", help="Filter: source doc UUID (repeatable)")


def _review_command(decision: str):
    def _cmd(
        assignment_id: str = typer.Argument(..., help="CodingAssignment aggregate UUID"),
        reviewer: str = typer.Option(..., "--reviewer", help="Reviewer identity"),
        note: str = typer.Option(
            "", "--note", help="Decision note (link fixes describe the change)"
        ),
    ) -> int:
        """Record one reviewer decision and advance the projections."""
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

        return _run_async(_run())

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
    return _run_async(_coding_run_coroutine(codebook_version_id, project_id, alpha))


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

    return _run_async(_ingest_coroutine(path, project_id, watch=False, force=force))


@research_app.command("ingest")
def research_ingest(
    path: str = typer.Argument(..., help="File, directory, or glob"),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    watch: bool = typer.Option(False, "--watch", help="Tail a drop directory forever"),
    force: bool = typer.Option(False, "--force", help="Re-ingest even if file hash unchanged"),
) -> int:
    """Dynamic ingestion with separate file-hash and text-hash dedup."""
    return _run_async(_ingest_coroutine(path, project_id, watch=watch, force=force))


@coding_app.command("inductive")
def coding_inductive(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
) -> int:
    """Inductive pass (v5 §9): summaries -> UMAP/HDBSCAN -> proposed codes."""
    return _run_async(_inductive_coroutine(project_id))


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
    return _run_async(
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


@research_app.command("demo")
def research_demo() -> int:
    """Narrated end-to-end pipeline on the bundled demo corpus (CLI only)."""

    from corpus_kb.config import load_config
    from corpus_kb.research.demo import run_demo

    # _run_async raises typer.Exit on any non-zero outcome (typer ignores a
    # plain int return), so a failed demo (embedder preflight, missing review
    # assignment) exits non-zero.
    return _run_async(run_demo(load_config()))


@research_app.command("cycle")
def research_cycle(
    mode: str = typer.Option(
        "out", "--mode", help="in: exactly one stage | on: approve each stage | out: to the gates"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Research project UUID"),
    drop_dir: str = typer.Option(None, "--dir", help="Transcript drop directory (ingest stage)"),
    question: str = typer.Option(None, "--question", help="Notebook question (final stage)"),
    codebook_version_id: str = typer.Option(
        None, "--codebook-version-id", help="Pin the codebook version (default: latest)"
    ),
    guide: bool = typer.Option(
        False, "--guide", help="Taught gates: narrate stages + halts with decision context"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Machine-readable stage/gate events (one JSON per line)"
    ),
    watch: str = typer.Option(
        None, "--watch", help="Foreground: re-run the cycle when transcripts land in this directory"
    ),
) -> int:
    """Research cycle (todo 20): full pipeline with human-critical halt gates.

    Halts print `AWAITING HUMAN: <gate>` plus the exact next command and a
    distinct per-gate exit code; `codebook_promotion` is hard-floored and
    can never be removed from the halt set. Resume by re-running this
    command after acting on the gate.
    """
    # _run_async raises typer.Exit on gate halts and denials (their exit
    # codes are the cycle's contract), so reaching here means exit 0.
    return _run_async(
        _cycle_coroutine(
            mode,
            project_id,
            drop_dir,
            question,
            codebook_version_id,
            guide,
            json_output,
            _cli_approve,
            watch,
        )
    )


def _cycle_coroutine(
    mode: str,
    project_id: str | None,
    drop_dir: str | None,
    question: str | None,
    codebook_version_id: str | None,
    guide: bool,
    json_output: bool,
    approve: object,
    watch: str | None,
):
    from typing import cast

    from corpus_kb.config import load_config
    from corpus_kb.research.cycle import run_cycle
    from corpus_kb.research.cycle_state import watch_cycle

    async def _run() -> int:
        import asyncpg

        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        kwargs: dict[str, object] = {
            "mode": mode,
            "project_id": _pid(project_id),
            "ingest_dir": drop_dir,
            "question": question,
            "codebook_version_id": _pid(codebook_version_id),
            "guide": guide,
            "json_output": json_output,
            "approve": cast("object", approve),
            "cfg": cfg,
        }
        try:
            if watch:
                return await watch_cycle(pool, _tenant(), watch, **kwargs)
            return await run_cycle(pool, _tenant(), **kwargs)
        finally:
            await pool.close()

    return _run()


@research_app.command("ask")
def research_ask(
    question: str = typer.Argument(..., help="Notebook question"),
    project_id: str = typer.Option(..., "--project-id", help="Research project UUID"),
    retrieval_only: bool = typer.Option(
        False, "--retrieval-only", help="Return evidence only (zero LLM calls)"
    ),
    k: int = typer.Option(6, "--k", min=1, help="Max evidence exchanges"),
    source_type: str = typer.Option(None, "--source-type", help="Filter: documents.source_type"),
    speaker_role: str = typer.Option(None, "--speaker-role", help="Filter: speaker role"),
    topic_id: int = typer.Option(None, "--topic-id", help="Filter: exchange topic id"),
    doc_id: list[str] = _DOC_ID_OPTION,
    code: str = typer.Option(None, "--code", help="Filter: code name or UUID"),
    min_confidence: str = typer.Option(None, "--min-confidence", help="Filter: high|medium|low"),
    review_status: str = typer.Option(
        None, "--review-status", help="Filter: auto|review|confirmed|overridden"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Grounded notebook Q&A: every sentence carries exchange-level citations."""
    return _run_async(
        _ask_coroutine(
            question,
            project_id,
            retrieval_only,
            k,
            source_type,
            speaker_role,
            topic_id,
            tuple(doc_id or ()),
            code,
            min_confidence,
            review_status,
            json_output,
        )
    )


def _ask_coroutine(
    question: str,
    project_id: str,
    retrieval_only: bool,
    k: int,
    source_type: str | None,
    speaker_role: str | None,
    topic_id: int | None,
    doc_ids: tuple[str, ...],
    code: str | None,
    min_confidence: str | None,
    review_status: str | None,
    json_output: bool,
):
    import json

    import asyncpg

    from corpus_kb.config import load_config

    async def _run() -> int:
        from corpus_kb.handlers.llm_handler import LlmHandler
        from corpus_kb.projections.research._embed import ResearchEmbedder
        from corpus_kb.rag import create_embedder
        from corpus_kb.research.notebook import NotebookQuery, notebook_ask

        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            llm = None if retrieval_only else LlmHandler(cfg)
            result = await notebook_ask(
                pool,
                embedder,
                llm,
                NotebookQuery(
                    question=question,
                    tenant_id=_tenant(),
                    project_id=cast(UUID, _pid(project_id)),
                    k=k,
                    retrieval_only=retrieval_only,
                    source_type=source_type,
                    speaker_role=speaker_role,
                    topic_id=topic_id,
                    doc_ids=tuple(cast(UUID, _pid(d)) for d in doc_ids),
                    code=code,
                    min_confidence=min_confidence,
                    review_status=review_status,
                ),
            )
        finally:
            await pool.close()
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_ask_result(result)
        if result.get("status") == "error":
            return 2
        return 0

    return _run()


def _print_ask_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    status = str(result.get("status", ""))
    if status == "empty":
        print(guide_copy.NOTEBOOK_EMPTY_HEADER)
        print(str(result.get("message", NO_EVIDENCE_TEXT)))
        return
    evidence = cast("list[dict[str, object]]", result.get("evidence") or [])
    sentences = cast("list[dict[str, object]]", result.get("sentences") or [])
    if sentences:
        for sentence in sentences:
            print(str(sentence.get("text")))
        print()
    if status == "evidence" and result.get("note"):
        print(str(result["note"]))
    print(guide_copy.NOTEBOOK_EVIDENCE_HEADER)
    for item in evidence:
        print(f"[{item.get('n')}] {_ask_locator(item)}")
        answer = str(item.get("highlighted_answer") or item.get("answer") or "")
        print(f"    {answer}")


NO_EVIDENCE_TEXT = "no evidence matches the requested filters"


def _ask_locator(item: dict[str, object]) -> str:
    parts = [str(item.get("title") or item.get("doc_id") or "untitled")]
    if item.get("speaker"):
        parts.append(str(item["speaker"]))
    if item.get("timestamp_s") is not None:
        ts = cast("float", item["timestamp_s"])
        parts.append(f"@ {ts:.0f}s")
    elif item.get("turn_range"):
        lo, hi = cast("list[int]", item["turn_range"])
        parts.append(f"turns {lo}-{hi}")
    return " - ".join(parts)


@research_app.command("evidence")
def research_evidence(
    code: str = typer.Argument(..., help="Code name or UUID"),
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    limit: int = typer.Option(200, "--limit", min=1, help="Max units per tab"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Evidence for code X: ranked units with disagreement/moderator/conformal flags."""
    return _run_async(_evidence_coroutine(code, project_id, version_id, limit, json_output))


def _evidence_coroutine(
    code: str,
    project_id: str | None,
    version_id: str | None,
    limit: int,
    json_output: bool,
):
    import json

    import asyncpg

    from corpus_kb.config import load_config

    async def _run() -> int:
        from corpus_kb.research.notebook_views import evidence_for_code

        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        pool = await asyncpg.create_pool(str(db.get("connection_string", "")))
        try:
            result = await evidence_for_code(
                pool,
                _tenant(),
                code,
                project_id=_pid(project_id),
                version_id=_pid(version_id),
                limit=limit,
            )
        finally:
            await pool.close()
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_evidence_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_evidence_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(f"{guide_copy.NOTEBOOK_EVIDENCE_FOR} {result.get('code')}: {result.get('n_units')} units")
    tabs = cast_dict(result.get("tabs"))
    for tab in ("explicit", "question_dependent"):
        print(f"\n{tab}:")
        entries = cast("list[dict[str, object]]", tabs.get(tab) or [])
        for entry in entries:
            flags = []
            if entry.get("disagreement"):
                flags.append("disagreement")
            if entry.get("moderator_introduced"):
                flags.append("moderator_introduced")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            print(
                f"  unit {entry.get('unit_id')} [{entry.get('confidence')}] "
                f"set_size={entry.get('conformal_set_size')}{flag_text}: "
                f"{str(entry.get('text'))[:120]}"
            )


@research_app.command("uncoded")
def research_uncoded(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    k: int = typer.Option(25, "--k", min=1, help="Max units to surface"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Uncoded/residual units ranked by nearest-code margin (missing-code radar)."""
    return _run_async(_uncoded_coroutine(project_id, version_id, k, json_output))


def _uncoded_coroutine(project_id: str | None, version_id: str | None, k: int, json_output: bool):
    import json

    import asyncpg

    from corpus_kb.config import load_config

    async def _run() -> int:
        from corpus_kb.projections.research._embed import ResearchEmbedder
        from corpus_kb.rag import create_embedder
        from corpus_kb.research.notebook_views import uncoded_units

        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        conn_str = str(db.get("connection_string", ""))
        pool = await asyncpg.create_pool(conn_str)
        try:
            embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
            result = await uncoded_units(
                pool,
                embedder,
                _tenant(),
                project_id=_pid(project_id),
                version_id=_pid(version_id),
                k=k,
            )
        finally:
            await pool.close()
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_uncoded_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_uncoded_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(guide_copy.NOTEBOOK_UNCODED_HEADER)
    for unit in cast("list[dict[str, object]]", result.get("units") or []):
        margin = cast("float", unit.get("margin", 0.0))
        print(f"  unit {unit.get('unit_id')} margin={margin:.3f}: {str(unit.get('text'))[:120]}")


@research_app.command("overlap")
def research_overlap(
    project_id: str = typer.Option(None, "--project-id", help="Restrict to one project"),
    version_id: str = typer.Option(None, "--codebook-version", help="Codebook version UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> int:
    """Code overlap: flagged pairs + shared-unit confusion + borderline units."""
    return _run_async(_overlap_coroutine(project_id, version_id, json_output))


def _overlap_coroutine(project_id: str | None, version_id: str | None, json_output: bool):
    import json

    import asyncpg

    from corpus_kb.config import load_config

    async def _run() -> int:
        from corpus_kb.research.notebook_views import overlap_view

        cfg = load_config()
        db = cast(dict[str, object], cfg.get("database") or {})
        pool = await asyncpg.create_pool(str(db.get("connection_string", "")))
        try:
            result = await overlap_view(
                pool,
                _tenant(),
                project_id=_pid(project_id),
                version_id=_pid(version_id),
            )
        finally:
            await pool.close()
        if json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_overlap_result(result)
        return 0 if result.get("status") == "ok" else 1

    return _run()


def _print_overlap_result(result: dict[str, object]) -> None:
    from corpus_kb.research import guide_copy

    if result.get("status") != "ok":
        print(str(result.get("message", "")))
        return
    print(guide_copy.NOTEBOOK_OVERLAP_HEADER)
    pairs = cast("list[dict[str, object]]", result.get("flagged_pairs") or [])
    if pairs:
        for pair in pairs:
            cos = cast("float", pair.get("cos", 0.0))
            print(f"  {pair.get('a')} <-> {pair.get('b')}: cos={cos:.3f}")
    else:
        print("  none above tau_overlap")
    borderline = cast("list[dict[str, object]]", result.get("borderline_units") or [])
    print(f"\nborderline top-2-margin units (< {result.get('delta_amb')}): {len(borderline)}")
    for unit in borderline:
        print(f"  unit {unit.get('unit_id')}: {str(unit.get('text'))[:100]}")


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
    return _run_async(_report_coroutine(codebook_version_id, project_id, level, json_output))


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
        print()
        anchors = _mapping(view).get("doc_links") or guide_copy.REPORT_DOC_ANCHORS
        for section, link in anchors.items():
            print(f"guide[{section}]: {link}")
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

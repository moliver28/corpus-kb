"""``corpus-kb research demo`` - narrated end-to-end run on the bundled corpus.

Runs the FULL research pipeline on docs/demo-corpus/ (two curated interview
transcripts + a seeded deductive codebook), narrating each stage from
guide_copy (the sole teaching-prose source) with a doc pointer per stage.
CLI-ONLY by design: no MCP tool is registered for it.

The corpus resolves REPO-ROOT-RELATIVE (editable install pinned - CI and
fresh-clone walkthroughs both install ``-e .``), NOT as package data.

Every stage reuses the exact production surfaces the CLI commands wrap
(ResearchHandler.ingest_transcript, run_deductive, execute_review,
build_report, notebook_ask) so the demo cannot drift from the real pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from corpus_kb.research import guide_copy

DEMO_CORPUS_ROOT = Path(__file__).resolve().parents[3] / "docs/demo-corpus"
TRANSCRIPTS = ("interview_grove_street.txt", "interview_eastside.txt")
CODEBOOK_FILE = "codebook.json"
DEMO_REVIEWER = "demo-reviewer"
DEMO_QUESTION = "How do transportation problems shape food access for residents?"

# Fixed demo project id (uuid5 of a stable name): re-runs dedup onto it.
DEMO_PROJECT_ID = uuid5(NAMESPACE_URL, "corpus-kb-demo/food-access")


@dataclass(frozen=True)
class DemoStage:
    """One narrated pipeline stage (pure data; offline tests assert wiring)."""

    name: str
    explain: str
    doc: str


def demo_stages() -> tuple[DemoStage, ...]:
    """The narrated stage list, in execution order."""
    return (
        DemoStage("ingest", guide_copy.DEMO_STAGE_INGEST, guide_copy.DEMO_STAGE_INGEST_DOC),
        DemoStage("codebook", guide_copy.DEMO_STAGE_CODEBOOK, guide_copy.DEMO_STAGE_CODEBOOK_DOC),
        DemoStage("coding", guide_copy.DEMO_STAGE_CODING, guide_copy.DEMO_STAGE_CODING_DOC),
        DemoStage("review", guide_copy.DEMO_STAGE_REVIEW, guide_copy.DEMO_STAGE_REVIEW_DOC),
        DemoStage("report", guide_copy.DEMO_STAGE_REPORT, guide_copy.DEMO_STAGE_REPORT_DOC),
        DemoStage("ask", guide_copy.DEMO_STAGE_ASK, guide_copy.DEMO_STAGE_ASK_DOC),
    )


def _narrate(stage: DemoStage) -> None:
    print(f"\n=== {stage.name.upper()} ===")
    print(stage.explain)
    print(f"docs: {stage.doc}")


def load_demo_codebook(root: Path | None = None) -> dict[str, Any]:
    """Parse + minimally validate the bundled codebook JSON."""
    path = (root or DEMO_CORPUS_ROOT) / CODEBOOK_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    codes = data.get("codes")
    if not isinstance(codes, list) or not codes:
        raise ValueError(f"{path}: codebook must define a non-empty 'codes' list")
    for code in codes:
        if not code.get("name") or not code.get("definition") or not code.get("exemplars"):
            raise ValueError(f"{path}: every code needs name, definition, exemplars")
    return data


def _wiring(pool: Any, cfg: dict[str, Any], conn_str: str) -> tuple[Any, ...]:
    """The shared production stack every research CLI command builds."""
    from corpus_kb.handlers.research_handler import ResearchHandler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder

    app = _get_app(conn_str)
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    # A per-run handler (not the process-wide singleton): run_demo owns this
    # pool's lifecycle, and a singleton created by an earlier command in the
    # same process would hold an already-closed pool.
    handler = ResearchHandler(pool)
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    checkpoint = CheckpointManager(pool)
    dlq = DLQHandler(pool)
    projection = ResearchProjection(pool, checkpoint, dlq, embedder)
    docs = DocumentsProjection(pool, checkpoint, dlq)
    embeds = EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq)
    return reader, handler, embedder, projection, docs, embeds


def _get_app(conn_str: str) -> Any:
    from corpus_kb.domain.application import get_app

    return get_app(conn_str)


async def _catch_up(reader: Any, projection: Any, docs: Any, embeds: Any, tenant_id: UUID) -> None:
    await projection.catch_up(reader)
    await docs.catch_up(reader, tenant_id)
    await embeds.catch_up(reader, tenant_id)


def _default_tenant() -> UUID:
    from corpus_kb.projections.research._common import DEFAULT_TENANT

    return DEFAULT_TENANT


def _conn_str(cfg: dict[str, Any]) -> str:
    return str((cfg.get("database", {}) or {}).get("connection_string", ""))


async def _resolve_exemplar_shas(
    pool: Any, tenant_id: UUID, codebook: dict[str, Any]
) -> dict[str, list[str]]:
    """Map each code name -> text_sha256 refs of its verbatim exemplar turns."""
    from corpus_kb.domain.transcript import normalize_turn_text, text_sha256
    from corpus_kb.storage.tenant_conn import tenant_connection

    wanted: set[str] = set()
    for code in codebook["codes"]:
        for exemplar in code["exemplars"]:
            wanted.add(text_sha256(normalize_turn_text(exemplar)))
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT text_sha256 FROM research_units WHERE text_sha256 = ANY($1)",
            sorted(wanted),
        )
    found = {row["text_sha256"] for row in rows}
    by_name: dict[str, list[str]] = {}
    for code in codebook["codes"]:
        shas = [
            text_sha256(normalize_turn_text(exemplar))
            for exemplar in code["exemplars"]
            if text_sha256(normalize_turn_text(exemplar)) in found
        ]
        by_name[str(code["name"])] = shas
    return by_name


async def _existing_codebook_version(
    pool: Any, tenant_id: UUID, codes: list[dict[str, Any]]
) -> UUID | None:
    """An already-seeded version with the same code content, or None.

    Re-run safety: the codebook_versions UNIQUE (tenant_id, sha256) key makes a
    byte-identical re-seed dead-letter on projection, so a second demo run
    reuses the first run's version instead (file/text hashes already make the
    ingest stage a no-op; this makes the codebook stage one too).
    """
    from corpus_kb.handlers.research_handler import codebook_sha256
    from corpus_kb.storage.tenant_conn import tenant_connection

    sha = codebook_sha256(codes)
    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT version_id FROM codebook_versions
            WHERE tenant_id = $1 AND sha256 = $2
            ORDER BY created_at LIMIT 1
            """,
            tenant_id,
            sha,
        )
    return UUID(str(row["version_id"])) if row else None


async def _seed_codebook(
    pool: Any,
    tenant_id: UUID,
    codes: list[dict[str, Any]],
    codebook: dict[str, Any],
    shas_by_code: dict[str, list[str]],
) -> UUID:
    """Seed one CodebookVersion aggregate (Created + CodeAdded + PrototypeUpdated)."""
    from corpus_kb.handlers.research_handler import ResearchHandler

    handler = ResearchHandler(pool)
    result = handler.create_codebook_version(
        tenant_id,
        str(codebook.get("label", "demo-codebook")),
        codes,
        notes=str(codebook.get("notes", "")),
    )
    version_id = UUID(str(result["version_id"]))
    version = handler.app.repository.get(version_id)
    for code, code_def in zip(codes, codebook["codes"], strict=True):
        refs = shas_by_code.get(str(code_def["name"]), [])
        if refs:
            version.update_prototypes(
                tenant_id=tenant_id,
                code_id=UUID(str(code["code_id"])),
                exemplar_text_sha256=refs,
            )
    handler.app.save(version)
    return version_id


async def _pending_review_assignment(pool: Any, tenant_id: UUID, project_id: UUID) -> UUID | None:
    """One pending (status='review') assignment id from THIS project, or None.

    Scoped to the demo project on purpose: a leftover escalation from an
    unrelated earlier run must not be accepted and narrated as this demo's
    coding result.
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT ra.assignment_aggregate_id
            FROM research_assignments ra
            JOIN research_units u
              ON u.unit_id = ra.unit_id AND u.tenant_id = ra.tenant_id
            JOIN documents d
              ON d.doc_id = u.doc_id AND d.tenant_id = ra.tenant_id
            WHERE ra.tenant_id = $1 AND ra.status = 'review' AND d.project_id = $2
            ORDER BY ra.assignment_aggregate_id LIMIT 1
            """,
            tenant_id,
            project_id,
        )
    return UUID(str(row["assignment_aggregate_id"])) if row else None


def _print_report_summary(view: dict[str, Any]) -> None:
    from corpus_kb.research.governance_report import _mapping

    for _name, light in _mapping(view.get("traffic_lights")).items():
        level = str(_mapping(light).get("level", "")).upper()
        message = str(_mapping(light).get("message", ""))
        print(f"[{level}] {message}")
    print()
    links = _mapping(view.get("doc_links"))
    for section, text in _mapping(view.get("plain_language")).items():
        print(f"- {text}")
        link = str(links.get(section, ""))
        if link:
            print(f"  guide[{section}]: {link}")


def _print_ask(result: dict[str, Any]) -> None:
    sentences = result.get("sentences") or []
    if sentences:
        for sentence in sentences:
            print(str(sentence.get("text")))
        print()
    if result.get("note"):
        print(str(result["note"]))
    print(guide_copy.NOTEBOOK_EVIDENCE_HEADER)
    for item in result.get("evidence") or []:
        locator = " - ".join(
            part
            for part in (
                str(item.get("title") or item.get("doc_id") or "untitled"),
                str(item.get("speaker")) if item.get("speaker") else "",
            )
            if part
        )
        print(f"[{item.get('n')}] {locator}")
        print(f"    {item.get('highlighted_answer') or item.get('answer') or ''}")


def embedder_blocked(cfg: dict[str, Any], pgml_installed: bool | None) -> bool:
    """True when the research embedder would abstain (the demo refuses to run).

    Pure gate so offline tests can pin the fail-fast contract without a
    database; run_demo probes pgml itself and passes the result here.
    """
    from corpus_kb._setup.doctor_research import embedder_check

    return embedder_check(cfg, pgml_installed).status != "ok"


async def run_demo(cfg: dict[str, Any] | None = None) -> int:
    """Execute the narrated pipeline; returns the process exit code."""
    import asyncpg

    from corpus_kb.config import load_config

    cfg = cfg if cfg is not None else load_config()
    conn_str = _conn_str(cfg)
    tenant_id = _default_tenant()

    print(guide_copy.DEMO_TITLE)
    print(guide_copy.DEMO_INTRO)
    print(f"docs: {guide_copy.DEMO_DOC}")

    stages = demo_stages()
    stage_ingest, stage_codebook, stage_coding, stage_review, stage_report, stage_ask = stages

    pool = await asyncpg.create_pool(conn_str)
    try:
        # Embedder honesty (todo-19): the default config ships provider=pgml,
        # and with the pgml extension absent every research embedding abstains.
        # Probe the live database and FAIL FAST with the fix - a demo that
        # limps through every stage and dead-ends at review teaches nothing.
        pgml_installed: bool | None = None
        emb_provider = str((cfg.get("embedding", {}) or {}).get("provider", ""))
        if emb_provider == "pgml":
            async with pool.acquire() as conn:
                present = await conn.fetchval(
                    "SELECT count(*) FROM pg_extension WHERE extname = 'pgml'"
                )
            pgml_installed = bool(present)
        if embedder_blocked(cfg, pgml_installed):
            print(f"ERROR: {guide_copy.DEMO_NO_EMBEDDINGS}")
            return 1

        _narrate(stage_ingest)
        reader, handler, _embedder, projection, docs, embeds = _wiring(pool, cfg, conn_str)
        project_id = DEMO_PROJECT_ID
        for name in TRANSCRIPTS:
            path = DEMO_CORPUS_ROOT / name
            await handler.ingest_transcript(tenant_id, str(path), project_id)
        await _catch_up(reader, projection, docs, embeds, tenant_id)
        print(f"ingested {len(TRANSCRIPTS)} transcripts into project {project_id}")

        _narrate(stage_codebook)
        codebook = load_demo_codebook()
        shas_by_code = await _resolve_exemplar_shas(pool, tenant_id, codebook)
        missing = [n for n, refs in shas_by_code.items() if not refs]
        if missing:
            print(f"ERROR: no ingested unit matches exemplars for codes: {missing}")
            return 1
        codes = [
            {
                "code_id": str(uuid5(NAMESPACE_URL, f"corpus-kb-demo/code/{code['name']}")),
                "name": code["name"],
                "definition": code["definition"],
                "inclusion": code.get("inclusion", ""),
                "exclusion": code.get("exclusion", ""),
            }
            for code in codebook["codes"]
        ]
        version_id = await _existing_codebook_version(pool, tenant_id, codes)
        if version_id is not None:
            print(f"codebook version {version_id} reused (seeded by an earlier run)")
        else:
            version_id = await _seed_codebook(pool, tenant_id, codes, codebook, shas_by_code)
            await _catch_up(reader, projection, docs, embeds, tenant_id)
            print(f"codebook version {version_id} seeded and projected")

        _narrate(stage_coding)
        from corpus_kb.research.deductive_run import run_deductive

        summary = await run_deductive(pool, tenant_id, version_id, project_id=project_id)
        await _catch_up(reader, projection, docs, embeds, tenant_id)
        print(
            f"units={summary.get('n_units')} codes={summary.get('n_codes')} "
            f"explicit={summary.get('explicit')} question_dependent="
            f"{summary.get('question_dependent')} review={summary.get('review')}"
        )

        _narrate(stage_review)
        from corpus_kb.research.review_surface import execute_review

        assignment_id = await _pending_review_assignment(pool, tenant_id, project_id)
        if assignment_id is None:
            print("ERROR: no pending review assignment found after the coding run")
            return 1
        review = await execute_review(
            pool, tenant_id, conn_str, assignment_id, DEMO_REVIEWER, "accept"
        )
        print(
            f"accepted assignment {review.get('assignment_id')} "
            f"(projection: {review.get('projection')})"
        )

        _narrate(stage_report)
        from corpus_kb.research.governance_report import novice_view, to_dict
        from corpus_kb.research.report_runner import build_report

        report = await build_report(pool, tenant_id, version_id, level=guide_copy.LEVEL_NOVICE)
        view = to_dict(report).get("novice_view") or novice_view(report)
        _print_report_summary(view)

        _narrate(stage_ask)
        from corpus_kb.handlers.llm_handler import LlmHandler
        from corpus_kb.projections.research._embed import ResearchEmbedder
        from corpus_kb.rag import create_embedder
        from corpus_kb.research.notebook import NotebookQuery, notebook_ask

        embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
        ask = await notebook_ask(
            pool,
            embedder,
            LlmHandler(cfg),
            NotebookQuery(
                question=DEMO_QUESTION,
                tenant_id=tenant_id,
                project_id=project_id,
            ),
        )
        _print_ask(ask)

        print()
        print(guide_copy.DEMO_OUTRO)
        return 0
    finally:
        await pool.close()

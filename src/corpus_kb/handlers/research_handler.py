"""Research command handler (todo-11 (b)) — EVENT-ONLY by pinned decision (r9).

Every command constructs aggregates and persists via
`corpus_kb.domain.application.get_app()` saves. Handlers NEVER write read
tables directly; read models are built exclusively by the research
projection. The ONLY non-event write is the ingest-owned operational state:
the content-addressed turn store (research_transcript_text) and the
ingested_files ledger — neither is a read model (one is a reference store
excluded from rebuilds, the other records mtime/size that are not
event-derivable).

LEGACY COEXISTENCE (r9, durable design decision): the legacy repo-ingest
path (command_handler.run_pipeline) stays direct-write under the IS-4
freeze; retirement of that dual path is a named post-pivot follow-up.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import get_app
from corpus_kb.domain.codebook import CodebookVersion
from corpus_kb.domain.coding import CodingAssignment, CodingRun
from corpus_kb.research.batching import gather_bounded
from corpus_kb.research.dynamic_ingest import (
    plan_file,
    scan_path,
)
from corpus_kb.research.linking import link_exchanges
from corpus_kb.research.roles import map_roles, speaker_role_basis
from corpus_kb.research.transcript_parser import parse_transcript
from corpus_kb.tools.ingest_common import file_ingest_concurrency
from corpus_kb.tools.ingest_store import PostgresIngestStore
from corpus_kb.tools.pipeline.research_units import research_document
from corpus_kb.tools.pipeline.sinks import UnitSink
from corpus_kb.tools.pipeline.validate import resolve_mode, validate_document

logger = logging.getLogger(__name__)

MAX_TURNS_PER_EVENT = 50
MAX_EXCHANGES_PER_EVENT = 100


def codebook_sha256(codes: list[dict[str, Any]]) -> str:
    """Content sha of a code set — the codebook_versions UNIQUE(tenant, sha256) key.

    Module-level so callers (e.g. the demo's re-run-safe seed) can look up an
    existing version without duplicating this formula.
    """
    return hashlib.sha256(repr(sorted(str(c) for c in codes)).encode()).hexdigest()


class ResearchHandler:
    """Event-only command surface for the research domain."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embed_fn: Callable[[str], list[float]] | None = None,
        dsn: str | None = None,
    ) -> None:
        self._pool = pool
        self._embed_fn = embed_fn
        self._dsn = dsn
        self._default_embed_fn: Callable[[str], list[float]] | None | None = None

    @property
    def app(self):
        # Bind the event store to THIS handler's database. A bare get_app()
        # resolves env/config, which is not the database self._pool reads
        # whenever the pool was built from an explicit DSN (the research
        # fixtures' scratch DB, any duckdb backend) — the app cache is keyed
        # on (backend, DSN), so the explicit DSN keeps aggregate writes on
        # the same store the pool reads. None keeps the env/config
        # resolution (the production default).
        return get_app(self._dsn) if self._dsn else get_app()

    def _link_embed(self) -> Callable[[str], list[float]] | None:
        """Sync embed fn for cross_ref linking (todo-11 deferred, wired todo-12).

        cosine over moderator questions is document-side similarity: RAW
        texts on both sides (instruct() is retrieval-query-side only).
        Ollama down or below-dim model -> zero vectors -> cosine 0 -> the
        linking layer falls back to adjacency, so this never breaks ingest.
        """
        if self._embed_fn is not None:
            return self._embed_fn
        if self._default_embed_fn is None:
            from corpus_kb.rag.embedder import OllamaEmbedder, create_embedder

            embedder = create_embedder()
            self._default_embed_fn = (
                embedder.embed if isinstance(embedder, OllamaEmbedder) else None
            )
        return self._default_embed_fn

    async def ingest_transcript(
        self,
        tenant_id: UUID,
        path: str,
        project_id: UUID | None = None,
        title: str | None = None,
        force: bool = False,
        validate_mode: str = "off",
    ) -> dict[str, Any]:
        """One transcript file -> canonical document + turn store + events.

        The front stages follow the pipeline contract (unified-ingestion
        Wave 3): parse (research segmentation unchanged), canonical
        IngestDocument with one span per turn, then the store stage through
        the UnitSink write port. Unit rows still land through the events
        (the projector upserts them, spans included).

        ``validate_mode`` is the Wave 4 validation gate (off | advisory |
        enforce | calibrate). Validators are read-only and run BEFORE the
        first write; ``enforce`` fails closed, ``advisory``/``calibrate``
        attach the summary to the result (the research path's run metadata).
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(path)
        decision = await plan_file(self._pool, tenant_id, file_path, force=force)
        if decision.skipped:
            return {
                "status": "skipped",
                "path": path,
                "reason": decision.reason,
                "file_sha256": decision.file_sha256,
            }

        parsed = parse_transcript(file_path)
        canonical = research_document(str(file_path), parsed.source_type, parsed.turns)
        if project_id is not None:
            # Link the run to its project (migration 036), best effort. The
            # research path keeps no corpus ingest_runs row (r9, the events
            # are the ledger), so the write targets this path's run identity
            # (the document id, per the UnitSink port) and matches zero rows
            # when no ledger row carries it. set_run_project never raises;
            # the guard keeps that contract local so ingest can never fail
            # because of this link.
            try:
                await PostgresIngestStore(self._pool, str(tenant_id)).set_run_project(
                    str(canonical.document.document_id), str(project_id)
                )
            except Exception as exc:
                logger.warning("Could not link ingest run to project %s: %s", project_id, exc)
        # Wave 4 validation gate: read-only, pre-write. resolve_mode raises
        # the typed ValidationError for an unknown mode before anything runs.
        validation_report: dict[str, Any] | None = None
        mode = resolve_mode(validate_mode)
        if mode != "off":
            validation_report = validate_document(canonical, mode)
        role_mapping = map_roles(parsed.turns)
        exchanges = link_exchanges(parsed.turns, embed=self._link_embed())

        texts_by_sha: dict[str, str] = {
            str(chunk.chunk_id): chunk.text for chunk in canonical.chunks
        }
        sink = UnitSink(self._pool, tenant_id)
        await sink.store_turn_texts(texts_by_sha, media_type=parsed.source_type)

        doc = Document(
            tenant_id=tenant_id,
            source=str(file_path.resolve()),
            source_type="transcript",
            file_size=file_path.stat().st_size,
            file_hash=decision.file_sha256,
            metadata={
                "project_id": str(project_id) if project_id else None,
                "title": title or file_path.stem,
                "source_hash": decision.file_sha256,
                "parser_name": "research.transcript_parser",
                "parser_version": "1",
                "parser_format": parsed.format_name,
                "parse_quality": parsed.parse_quality,
                "project_name": title or file_path.stem,
                "role_basis": speaker_role_basis(parsed.turns),
                "role_mapping": role_mapping,
            },
        )
        for batch in _batches(parsed.turns, MAX_TURNS_PER_EVENT):
            doc.add_turn_batch(tenant_id=tenant_id, turns=[turn.to_payload() for turn in batch])
        for batch in _batches(exchanges, MAX_EXCHANGES_PER_EVENT):
            doc.add_exchange_batch(tenant_id=tenant_id, exchanges=[e.to_payload() for e in batch])
        self.app.save(doc)

        await sink.record_ledger(
            file_path,
            decision.file_sha256,
            decision.source_type,
            UUID(str(doc.id)),
        )
        result: dict[str, Any] = {
            "status": "success",
            "doc_id": str(doc.id),
            "path": path,
            "n_turns": len(parsed.turns),
            "n_exchanges": len(exchanges),
            "source_type": decision.source_type,
            "parse_quality": parsed.parse_quality,
            "file_sha256": decision.file_sha256,
        }
        # Advisory and calibrate both attach here (no stages ledger on the
        # research path): the result dict IS the run metadata Wave 7 reads.
        if validation_report is not None:
            result["validation"] = validation_report
        return result

    async def ingest_path(
        self,
        tenant_id: UUID,
        path: str,
        project_id: UUID | None = None,
        force: bool = False,
        validate_mode: str = "off",
    ) -> dict[str, Any]:
        """Dynamic ingestion: file, directory, or glob (per-file auto-detect)."""
        files = await scan_path(Path(path))
        concurrency = file_ingest_concurrency(load_config(), self._pool)

        async def _ingest_one(file_path: Path) -> dict[str, Any]:
            try:
                return await self.ingest_transcript(
                    tenant_id, str(file_path), project_id, force=force, validate_mode=validate_mode
                )
            except Exception as exc:
                logger.warning("ingest failed for %s: %s", file_path, exc)
                return {"status": "error", "path": str(file_path), "error": str(exc)}

        results = await gather_bounded(files, _ingest_one, concurrency)
        return {
            "status": "success",
            "files_found": len(files),
            "ingested": sum(1 for r in results if r.get("status") == "success"),
            "skipped": sum(1 for r in results if r.get("status") == "skipped"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
            "results": results,
        }

    def create_codebook_version(
        self, tenant_id: UUID, label: str, codes: list[dict[str, Any]], notes: str = ""
    ) -> dict[str, Any]:
        sha = codebook_sha256(codes)
        version = CodebookVersion(tenant_id=tenant_id, label=label, sha256=sha, notes=notes)
        version.add_codes(tenant_id=tenant_id, codes=codes)
        self.app.save(version)
        return {"status": "success", "version_id": str(version.id), "label": label, "sha256": sha}

    def start_coding_run(
        self,
        tenant_id: UUID,
        llm_name: str = "",
        embed_model: str = "",
        model_revision: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = CodingRun(
            tenant_id=tenant_id,
            llm_name=llm_name,
            embed_model=embed_model,
            model_revision=model_revision,
            params=params or {},
        )
        self.app.save(run)
        return {"status": "success", "run_id": str(run.id)}

    def checkpoint_coding_run(
        self, tenant_id: UUID, run_id: UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._load_run(run_id)
        run.add_checkpoint(tenant_id=tenant_id, payload=payload)
        self.app.save(run)
        return {"status": "success", "run_id": str(run_id), "state": run.state}

    def stop_coding_run(self, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
        run = self._load_run(run_id)
        run.stop(
            tenant_id=tenant_id,
            stopped_at=datetime.now(tz=UTC).isoformat(),
        )
        self.app.save(run)
        return {"status": "success", "run_id": str(run_id), "state": run.state}

    def record_assignment(self, tenant_id: UUID, **kwargs: object) -> dict[str, Any]:
        """Record one per-unit assignment (per-unit aggregate => no contention)."""
        # Decision routing: explicit status wins (deductive v2 routing rides the
        # event); when the caller omits it, derive from evidence_basis/stance so
        # the question-dependent / deny / deflect -> review contract holds for
        # legacy callers (assignment_projector mirrors this derivation for
        # events recorded before the status field existed).
        status = str(kwargs.get("status") or "")
        if status not in ("auto", "review"):
            if kwargs.get("evidence_basis") == "question_dependent" or kwargs.get("stance") in (
                "deny",
                "deflect",
            ):
                status = "review"
            else:
                status = "auto"
        assignment = CodingAssignment(
            tenant_id=tenant_id,
            unit_id=int(cast("int", kwargs["unit_id"])),
            code_id=UUID(str(kwargs["code_id"])),
            run_id=UUID(str(kwargs["run_id"])),
            cb_version_id=UUID(str(kwargs["cb_version_id"]))
            if kwargs.get("cb_version_id")
            else None,
            sim_answer=cast("float | None", kwargs.get("sim_answer")),
            sim_qa=cast("float | None", kwargs.get("sim_qa")),
            sim_q=cast("float | None", kwargs.get("sim_q")),
            evidence_basis=cast("str | None", kwargs.get("evidence_basis")),
            stance=cast("str | None", kwargs.get("stance")),
            term_origin=cast("str | None", kwargs.get("term_origin")),
            rationale=str(kwargs.get("rationale", "")),
            confidence=cast("str | None", kwargs.get("confidence")),
            tier_fired=cast("int | None", kwargs.get("tier_fired")),
            status=status,
        )
        self.app.save(assignment)
        if kwargs.get("signals"):
            assignment.record_signals(
                tenant_id=tenant_id,
                unit_id=int(cast("int", kwargs["unit_id"])),
                run_id=UUID(str(kwargs["run_id"])),
                signals=list(cast("list[dict[str, object]]", kwargs["signals"])),
            )
            self.app.save(assignment)
        return {
            "status": "success",
            "assignment_id": str(assignment.id),
            "unit_id": kwargs["unit_id"],
        }

    def review_assignment(
        self,
        tenant_id: UUID,
        assignment_aggregate_id: UUID,
        reviewer: str,
        decision: str,
        note: str = "",
    ) -> dict[str, Any]:
        assignment = self.app.repository.get(assignment_aggregate_id)
        if not isinstance(assignment, CodingAssignment):
            raise ValueError(f"aggregate {assignment_aggregate_id} is not a CodingAssignment")
        assignment.review(tenant_id=tenant_id, reviewer=reviewer, decision=decision, note=note)
        self.app.save(assignment)
        return {"status": "success", "assignment_id": str(assignment.id), "decision": decision}

    def set_keywords(
        self, tenant_id: UUID, version_id: UUID, code_id: UUID, keywords: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Set one code's synthesized keyword list (KeywordSetUpdated event)."""
        version = self.app.repository.get(version_id)
        if not isinstance(version, CodebookVersion):
            raise ValueError(f"aggregate {version_id} is not a CodebookVersion")
        version.update_keywords(tenant_id=tenant_id, code_id=code_id, keywords=keywords)
        self.app.save(version)
        return {
            "status": "success",
            "version_id": str(version_id),
            "code_id": str(code_id),
            "n_keywords": len(keywords),
        }

    def _load_run(self, run_id: UUID) -> CodingRun:
        run = self.app.repository.get(run_id)
        if not isinstance(run, CodingRun):
            raise ValueError(f"aggregate {run_id} is not a CodingRun")
        return run


def _batches(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


_handler: ResearchHandler | None = None


def get_research_handler(
    pool: asyncpg.Pool | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> ResearchHandler:
    global _handler
    if _handler is None:
        if pool is None:
            raise RuntimeError("ResearchHandler requires an asyncpg pool")
        _handler = ResearchHandler(pool, embed_fn=embed_fn)
    return _handler


def reset_research_handler() -> None:
    global _handler
    _handler = None

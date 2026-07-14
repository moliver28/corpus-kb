"""Command handler — dispatches commands to domain aggregates.

Wraps the ingest pipeline (partition → chunk → embed → extract → store)
and creates eventsourcing aggregates + events for audit trail.

Usage:
    from handlers.command_handler import get_command_handler
    handler = get_command_handler(pool)
    result = await handler.handle_ingest_file(IngestFileCommand(file_path='test.py'))
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg

from domain.aggregates import Document, Entity, Relation
from domain.application import get_app
from domain.models import (
    AddEntityCommand,
    AddRelationCommand,
    DeleteDocumentCommand,
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
)
from tools.ingest_common import load_config_or_pass, run_pipeline

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


class CommandHandler:
    """Dispatches commands to domain aggregates via the eventsourcing app.

    For ingest commands: calls the ingest pipeline (async), then
    creates a Document aggregate + fires events for audit trail.
    For entity/relation commands: creates aggregates directly.
    """

    def __init__(
        self,
        config: Optional[dict[str, object]] = None,
        pool: Optional[asyncpg.Pool] = None,
    ) -> None:
        self._config = load_config_or_pass(config)
        self._pool = pool
        self._app = None  # lazy init — only needed when Postgres is up

    @property
    def app(self):
        """Lazy-load the eventsourcing app (requires Postgres)."""
        if self._app is None:
            self._app = get_app()
        return self._app

    async def handle_ingest_file(self, cmd: IngestFileCommand) -> dict[str, object]:
        """Ingest a file: run pipeline → create Document aggregate → fire events."""
        path = Path(cmd.file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {cmd.file_path}")

        text = path.read_text(encoding="utf-8")
        source_type = cmd.source_type or _detect_source_type(cmd.file_path)

        return await self._run_ingest(text, source_type, cmd.file_path, cmd.tenant_id)

    async def handle_ingest_text(self, cmd: IngestTextCommand) -> dict[str, object]:
        """Ingest raw text: run pipeline → create Document aggregate → fire events."""
        return await self._run_ingest(cmd.text, cmd.source_type, cmd.source, cmd.tenant_id)

    async def handle_ingest_directory(self, cmd: IngestDirectoryCommand) -> dict[str, object]:
        """Ingest all files in a directory."""
        dir_path = Path(cmd.directory_path)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {cmd.directory_path}")

        results: list[dict[str, object]] = []
        pattern = "**/*" if cmd.recursive else "*"
        for file_path in sorted(dir_path.glob(pattern)):
            if file_path.is_file():
                try:
                    result = await self.handle_ingest_file(
                        IngestFileCommand(
                            tenant_id=cmd.tenant_id,
                            file_path=str(file_path),
                        )
                    )
                    results.append(result)
                except Exception as exc:
                    logger.warning("Failed to ingest %s: %s", file_path, exc)
                    results.append(
                        {"status": "error", "path": str(file_path), "error": str(exc)}
                    )

        return {
            "status": "success",
            "directory": cmd.directory_path,
            "files_processed": len(results),
            "results": results,
        }

    def handle_add_entity(self, cmd: AddEntityCommand) -> dict[str, object]:
        """Add an entity to the knowledge graph via aggregate."""
        entity = Entity(
            tenant_id=cmd.tenant_id,
            name=cmd.name,
            entity_type=cmd.entity_type,
            metadata=cmd.metadata,
        )
        self.app.save(entity)
        logger.info("Created entity %s (%s)", cmd.name, cmd.entity_type)
        return {
            "status": "success",
            "entity_id": str(entity.id),
            "name": cmd.name,
            "entity_type": cmd.entity_type,
        }

    def handle_add_relation(self, cmd: AddRelationCommand) -> dict[str, object]:
        """Add a relation between two entities via aggregate."""
        relation = Relation(
            tenant_id=cmd.tenant_id,
            source_entity_id=cmd.source_entity_id,
            target_entity_id=cmd.target_entity_id,
            relation_type=cmd.relation_type,
            weight=cmd.weight,
            metadata=cmd.metadata,
        )
        self.app.save(relation)
        logger.info(
            "Created relation %s -> %s (%s)",
            cmd.source_entity_id,
            cmd.target_entity_id,
            cmd.relation_type,
        )
        return {
            "status": "success",
            "relation_id": str(relation.id),
            "relation_type": cmd.relation_type,
        }

    def handle_delete_document(self, cmd: DeleteDocumentCommand) -> dict[str, object]:
        """Delete a document by marking the aggregate as deleted."""
        doc_events = list(self.app.repository.get(cmd.doc_id))
        if not doc_events:
            raise ValueError(f"Document not found: {cmd.doc_id}")

        doc = Document.reconstruct(cmd.doc_id, doc_events)
        doc.delete()
        self.app.save(doc)
        logger.info("Deleted document %s", cmd.doc_id)
        return {"status": "success", "doc_id": str(cmd.doc_id)}

    async def _run_ingest(
        self,
        text: str,
        source_type: str,
        source_path: str,
        tenant_id: UUID,
    ) -> dict[str, object]:
        """Run the ingest pipeline and create eventsourcing aggregates.

        The pipeline (ingest_common.run_pipeline) handles:
        partition → chunk → embed → extract → store (Postgres directly)

        After the pipeline succeeds, we create a Document aggregate and
        fire Ingested + ChunksAdded events for the event sourcing audit trail.
        """
        if self._pool is None:
            raise RuntimeError("CommandHandler requires an asyncpg.Pool for ingest.")

        # 1. Run the pipeline (async, writes directly to Postgres)
        result = await run_pipeline(
            text,
            source_type,
            source_path,
            self._config,
            self._pool,
            str(tenant_id),
        )

        if result.get("status") != "success":
            return result

        # 2. Create Document aggregate + fire events (audit trail)
        try:
            doc = Document(
                tenant_id=tenant_id,
                source=source_path,
                source_type=source_type,
                file_size=result.get("size_bytes"),
            )
            doc.add_chunks(
                chunk_count=result.get("chunk_count", 0),
                chunk_texts=[],  # text stored in projection, not in event
            )
            self.app.save(doc)
            result["aggregate_id"] = str(doc.id)
            result["aggregate_version"] = doc.version
        except Exception as exc:
            # Pipeline succeeded but event sourcing failed — log and continue
            logger.warning("Event sourcing failed (pipeline succeeded): %s", exc)
            result["event_sourcing_error"] = str(exc)

        return result


def _detect_source_type(file_path: str) -> str:
    """Detect source type from file extension."""
    ext = Path(file_path).suffix.lower()
    if ext in (".py", ".js", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cpp", ".h"):
        return "code"
    if ext in (".md", ".rst", ".txt"):
        return "markdown" if ext != ".txt" else "text"
    return "text"


# ============================================================================
# Singleton
# ============================================================================

_command_handler: Optional[CommandHandler] = None


def get_command_handler(
    config: Optional[dict[str, object]] = None,
    pool: Optional[asyncpg.Pool] = None,
) -> CommandHandler:
    """Get or create the singleton CommandHandler."""
    global _command_handler
    if _command_handler is None:
        _command_handler = CommandHandler(config, pool)
    return _command_handler


def reset_command_handler() -> None:
    """Reset the singleton (for testing)."""
    global _command_handler
    _command_handler = None
"""Command handler — the event-sourcing envelope over the shared ingest tools.

The INGEST pipeline lives in the tool layer (``tools.ingest_tools.ingest_file``
— binary-safe, strategy-aware; ``tools.ingest_common.ingest_text`` — document
type whitelist). CLI, MCP, HTTP and socket surfaces all go through those ONE
implementations; this handler keeps only the eventsourcing responsibility:
run the shared tool function, then create a Document aggregate and fire
Ingested + ChunksAdded events for the audit trail.

Envelope contract (zero drift across surfaces): the HTTP/socket response is
the tool envelope EXACTLY, plus the DECLARED additive wrapper keys
``aggregate_id`` / ``aggregate_version`` (and ``event_sourcing_error`` only
when event sourcing failed after a successful pipeline).

``source_type`` means ONE thing on every surface: on file ingest it names an
optional INGESTION STRATEGY (validated against the registry); on text ingest
it is the DOCUMENT type under the shared whitelist. The detected document
type comes back in the envelope and is copied into the event row, so the
ledger stays coherent.

Usage:
    from corpus_kb.handlers.command_handler import get_command_handler
    handler = get_command_handler(pool)
    result = await handler.handle_ingest_file(IngestFileCommand(file_path='test.py'))
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from corpus_kb.domain.aggregates import Document, Entity, Relation
from corpus_kb.domain.application import get_app
from corpus_kb.domain.models import (
    AddEntityCommand,
    AddRelationCommand,
    DeleteDocumentCommand,
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
)
from corpus_kb.tools.ingest_common import ingest_text as ingest_text_tool
from corpus_kb.tools.ingest_common import load_config_or_pass
from corpus_kb.tools.ingest_tools import ingest_file as ingest_file_tool

logger = logging.getLogger(__name__)


class CommandHandler:
    """Dispatches commands to domain aggregates via the eventsourcing app.

    For ingest commands: run the shared tool pipeline (partition → chunk →
    embed → extract → store), then create a Document aggregate + fire events
    for audit trail. For entity/relation commands: create aggregates directly.
    """

    def __init__(
        self,
        config: dict[str, object] | None = None,
        pool: asyncpg.Pool | None = None,
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
        """Ingest a file: shared tool pipeline → Document aggregate → events."""
        if self._pool is None:
            raise RuntimeError("CommandHandler requires an asyncpg.Pool for ingest.")
        result = await ingest_file_tool(
            cmd.file_path,
            self._pool,
            self._config,
            tenant_id=str(cmd.tenant_id),
            source_type=cmd.source_type,
        )
        return self._attach_events(result, source=cmd.file_path, tenant_id=cmd.tenant_id)

    async def handle_ingest_text(self, cmd: IngestTextCommand) -> dict[str, object]:
        """Ingest raw text: shared tool pipeline (whitelist included) → events."""
        if self._pool is None:
            raise RuntimeError("CommandHandler requires an asyncpg.Pool for ingest.")
        result = await ingest_text_tool(
            cmd.text,
            self._pool,
            source_type=cmd.source_type,
            config=self._config,
            tenant_id=str(cmd.tenant_id),
            source=cmd.source,
        )
        return self._attach_events(result, source=cmd.source, tenant_id=cmd.tenant_id)

    async def handle_ingest_directory(self, cmd: IngestDirectoryCommand) -> dict[str, object]:
        """Ingest all files in a directory (each file via the shared tool path)."""
        from pathlib import Path

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
                    results.append({"status": "error", "path": str(file_path), "error": str(exc)})

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

    def _attach_events(
        self,
        result: dict[str, object],
        source: str,
        tenant_id: UUID,
    ) -> dict[str, object]:
        """Attach the eventsourcing audit trail to a SUCCESSFUL tool envelope.

        The Document aggregate records the DOCUMENT source type from the
        envelope (the pipeline's detection result) — never the caller's
        strategy override — so the event ledger stays coherent. Any
        non-success envelope (error / partial / skipped) passes through
        unchanged: the tool layer already reported honestly, and there is
        no completed ingest to audit.
        """
        if result.get("status") != "success":
            return result

        # 2. Create Document aggregate + fire events (audit trail)
        try:
            doc = Document(
                tenant_id=tenant_id,
                source=source,
                source_type=str(result.get("source_type", "text")),
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


# ============================================================================
# Singleton
# ============================================================================

_command_handler: CommandHandler | None = None


def get_command_handler(
    config: dict[str, object] | None = None,
    pool: asyncpg.Pool | None = None,
) -> CommandHandler:
    """Get or create the singleton CommandHandler.

    Passing ``pool`` on an existing singleton updates its pool, so a lazily
    wired surface (HTTP route / socket dispatch after server startup) always
    sees the live pool server_wiring created.
    """
    global _command_handler
    if _command_handler is None:
        _command_handler = CommandHandler(config, pool)
    elif pool is not None:
        _command_handler._pool = pool
    return _command_handler


def reset_command_handler() -> None:
    """Reset the singleton (for testing)."""
    global _command_handler
    _command_handler = None

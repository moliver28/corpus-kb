"""Command handler — the event-sourcing envelope over the shared tools.

The pipelines live in the tool layer: ``ingest_file``/``ingest_directory``
(binary-safe, strategy-aware; the directory walk is THE one walk),
``ingest_text`` (document type whitelist), and ``delete_document`` (real
tenant-honored row delete plus the aggregate audit). This handler runs
the shared tool function, then attaches the audit events
(``_attach_events`` / ``handlers.delete_audit.attach_delete_event``).

Envelope contract: the HTTP/socket response is the tool envelope EXACTLY,
plus the DECLARED additive wrapper keys ``aggregate_id``/``aggregate_version``
(``event_sourcing_error`` appears only when event sourcing failed after a
successful pipeline). ``source_type`` means ONE thing everywhere: an
ingestion-STRATEGY override on file ingest, the whitelisted DOCUMENT type
on text ingest.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.domain.aggregates import Document, Entity, Relation
from corpus_kb.domain.application import dsn_from_config, get_app
from corpus_kb.domain.models import (
    AddEntityCommand,
    AddRelationCommand,
    DeleteDocumentCommand,
    IngestDirectoryCommand,
    IngestFileCommand,
    IngestTextCommand,
)
from corpus_kb.handlers.delete_audit import attach_delete_event
from corpus_kb.tools.ingest_common import ingest_text as ingest_text_tool
from corpus_kb.tools.ingest_common import load_config_or_pass
from corpus_kb.tools.ingest_tools import delete_document as delete_document_tool
from corpus_kb.tools.ingest_tools import ingest_directory as ingest_directory_tool
from corpus_kb.tools.ingest_tools import ingest_file as ingest_file_tool

logger = logging.getLogger(__name__)


class CommandHandler:
    """Dispatches commands to domain aggregates via the eventsourcing app:
    ingest tools plus audit events; entity/relation aggregates directly.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = load_config_or_pass(config)
        self._pool = pool
        self._app = None  # lazy init — only needed when Postgres is up

    @property
    def app(self):
        """Lazy-load the eventsourcing app (Postgres pool or sqlite file).

        ``database.backend`` is the switch: the default keeps the bare
        ``get_app()`` resolution; ``duckdb`` keys the app cache on the
        config DSN with the ``eventsourcing.sqlite`` store, which must
        NOT be the analytics ``.duckdb`` (SQLite cannot open it).
        """
        if self._app is None:
            db_cfg = (self._config or {}).get("database", {})
            dsn = dsn_from_config(self._config)
            if str(db_cfg.get("backend") or "postgres") == "duckdb":
                self._app = get_app(dsn or "", backend="duckdb")
            else:
                self._app = get_app()
        return self._app

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("CommandHandler requires an asyncpg.Pool for ingest.")
        return self._pool

    async def handle_ingest_file(self, cmd: IngestFileCommand) -> dict[str, Any]:
        """Ingest a file: shared tool pipeline → Document aggregate → events."""
        result = await ingest_file_tool(
            cmd.file_path,
            self._require_pool(),
            self._config,
            tenant_id=str(cmd.tenant_id),
            source_type=cmd.source_type,
        )
        return self._attach_events(result, source=cmd.file_path, tenant_id=cmd.tenant_id)

    async def handle_ingest_text(self, cmd: IngestTextCommand) -> dict[str, Any]:
        """Ingest raw text: shared tool pipeline (whitelist included) → events."""
        result = await ingest_text_tool(
            cmd.text,
            self._require_pool(),
            source_type=cmd.source_type,
            config=self._config,
            tenant_id=str(cmd.tenant_id),
            source=cmd.source,
        )
        return self._attach_events(result, source=cmd.source, tenant_id=cmd.tenant_id)

    async def handle_ingest_directory(self, cmd: IngestDirectoryCommand) -> dict[str, Any]:
        """Ingest a directory through the ONE shared walk, then audit files.

        The tool owns the walk, the ``WATCH_SUFFIXES`` filter, and the
        aggregate envelope; this wrapper attaches the per-file audit
        events exactly like :meth:`handle_ingest_file` does per call.
        """
        result = await ingest_directory_tool(
            cmd.directory_path,
            self._require_pool(),
            self._config,
            tenant_id=str(cmd.tenant_id),
            recursive=cmd.recursive,
        )
        if result.get("status") != "success":
            return result
        files = result.get("results")
        if not isinstance(files, list):
            return result
        for file_result in files:
            if isinstance(file_result, dict) and file_result.get("status") == "success":
                self._attach_events(
                    file_result,
                    source=str(file_result.get("path", cmd.directory_path)),
                    tenant_id=cmd.tenant_id,
                )
        return result

    def handle_add_entity(self, cmd: AddEntityCommand) -> dict[str, Any]:
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

    def handle_add_relation(self, cmd: AddRelationCommand) -> dict[str, Any]:
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

    async def handle_delete_document(self, cmd: DeleteDocumentCommand) -> dict[str, Any]:
        """Delete one document through the ONE shared tool implementation.

        ``tools.ingest_tools.delete_document`` owns the real row delete
        (tenant-honored, idempotent); this wrapper then attaches the
        Deleted audit event — the ingest-audit additive pattern again.
        """
        result = await delete_document_tool(
            str(cmd.doc_id),
            self._require_pool(),
            self._config,
            tenant_id=str(cmd.tenant_id),
        )
        return attach_delete_event(result, str(cmd.doc_id))

    def _attach_events(
        self,
        result: dict[str, Any],
        source: str,
        tenant_id: UUID,
    ) -> dict[str, Any]:
        """Attach the audit trail to a SUCCESSFUL tool envelope.

        The aggregate records the envelope's DOCUMENT source type (never
        the caller's strategy override). Non-success envelopes pass through.
        """
        if result.get("status") != "success":
            return result

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


# --- Singleton ---------------------------------------------------------------

_command_handler: CommandHandler | None = None


def get_command_handler(
    config: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
) -> CommandHandler:
    """Get or create the singleton CommandHandler.

    Passing ``pool`` on an existing singleton updates its pool, so a
    lazily wired surface always sees the live pool server_wiring created.
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

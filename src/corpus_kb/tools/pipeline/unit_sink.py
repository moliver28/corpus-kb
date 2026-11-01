"""The research-unit write port (``UnitSink``), split from ``sinks``.

One sink per storage family keeps every file near the size gate: the
chunk-family port stays in ``sinks.py``; the research port lives here and
is re-exported from ``sinks`` so importers keep one path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS

from ...storage.gateway import SqlGateway
from ...utils.models import Chunk, Document, Entity, Relation

if TYPE_CHECKING:
    from ...research.conn import Store

logger = logging.getLogger(__name__)

__all__ = ["UnitSink"]


class UnitSink:
    """The research-unit write port: the IngestSink contract over research.

    Storage shape is event-sourced (the units land when the projector runs),
    so this sink owns NO SQL. Every leg delegates VERBATIM to the existing
    research seams: ``store_turn_texts`` and ``upsert_unit`` from
    ``projections/research/_common.py``, the ledger helpers from
    ``research/dynamic_ingest.py``. The corpus-artifact legs (chunks,
    vectors, entities, relations) have no research meaning; each reports an
    honest zero and never writes.
    """

    def __init__(self, store: Store, tenant_id: UUID | str) -> None:
        self._store = store
        self._tenant_id = UUID(str(tenant_id))

    @property
    def tenant_id(self) -> str:
        """The tenant all wrapped writes scope to."""
        return str(self._tenant_id)

    @property
    def gateway(self) -> SqlGateway:
        """The storage seam over the research store."""
        from ...research.conn import resolve_store

        return resolve_store(self._store)

    # -- research legs (the real writes) -----------------------------------

    async def store_turn_texts(self, texts_by_sha: dict[str, str], media_type: str) -> int:
        """Delegates verbatim to the content-addressed turn store."""
        from ...projections.research import _common

        return await _common.store_turn_texts(
            self._store, self._tenant_id, texts_by_sha, media_type
        )

    async def store_units(
        self,
        conn: Any,
        doc_id: UUID,
        project_id: str | None,
        turns: list[dict[str, Any]],
        texts: dict[str, str],
    ) -> int:
        """Emit units through the projector's upserts, in seq order.

        Same two writes per turn as the projector always made (speaker,
        then unit), on the CALLER's transaction connection. The seq sort is
        the determinism tiebreaker; payloads already arrive ordered.
        """
        from ...projections.research import _common

        written = 0
        for turn in sorted(turns, key=lambda payload: int(payload["seq"])):
            await _common.upsert_speaker(conn, self._tenant_id, doc_id, turn)
            if str(turn["text_sha256"]) not in texts:
                logger.warning(
                    "TurnsParsed references unknown text_sha256 %s (doc %s seq %s); "
                    "skipping unit — the content-addressed store is ingest-owned",
                    turn["text_sha256"],
                    doc_id,
                    turn.get("seq"),
                )
                continue
            await _common.upsert_unit(conn, self._tenant_id, doc_id, project_id, turn, texts)
            written += 1
        return written

    async def record_ledger(
        self, path: Path, file_sha256: str, source_type: str, doc_id: UUID | None
    ) -> None:
        """Delegates verbatim to the ingested-files ledger write."""
        from ...research.dynamic_ingest import ledger_record

        await ledger_record(self._store, self._tenant_id, path, file_sha256, source_type, doc_id)

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        """The file-ledger probe: the doc id for this exact content, or None."""
        from ...research.dynamic_ingest import ledger_doc_id

        doc_id = await ledger_doc_id(self._store, self._tenant_id, Path(source), file_hash)
        return None if doc_id is None else str(doc_id)

    # -- IngestSink port legs with no research write -----------------------

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]:
        """Identity mapping only: the domain events are the research ledger."""
        return str(document.document_id), file_hash

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, object],
        errors: list[str],
    ) -> None:
        """Nothing to close: research keeps no corpus ingest_runs row."""

    async def fail_run(self, run_id: str, error: str) -> None:
        """Nothing to close: research keeps no corpus ingest_runs row."""

    async def update_document(self, document: Document, file_hash: str) -> None:
        """Nothing to update: the documents row is projection-owned."""

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """No corpus chunk writes on the research path."""
        return set()

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = EMBEDDING_DIMENSIONS,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        """No corpus vector writes on the research path."""
        return 0

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        """No corpus entity writes on the research path."""
        return {}

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int:
        """No corpus relation writes on the research path."""
        return 0

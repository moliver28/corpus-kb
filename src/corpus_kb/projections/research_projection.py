"""Research projection façade (todo-11 (c)) — ONE checkpoint, decomposed.

Thin coordinator: subscription (EventReader over the eventsourcing lib's
notification_id sequence), checkpoint advancement (ONE ResearchProjection
position — the global monotonic bigserial, never created_at comparisons),
DLQ, and per-bounded-context dispatch. All table writes live in the
projectors under corpus_kb.projections.research.

Internal ordering: project/document/speaker/unit/exchange stages run BEFORE
code/assignment/signal/run/review stages (a single event batch is processed
strictly in notification_id order, which the ingest-then-code chronology
already satisfies; within one event, stage order is explicit below).

RLS: every write runs through tenant_connection (set_config AND the writes
in ONE transaction) — the separate-statement set_config the legacy
projections used is defeated by FORCE RLS and is never copied here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader, event_timestamp_dt
from corpus_kb.projections.ids import deterministic_event_id
from corpus_kb.projections.research._common import (
    DEFAULT_TENANT,
    RESEARCH_PROJECTION_NAME,
    event_payload,
    require_tenant,
)
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research.analytics_projector import AnalyticsProjector
from corpus_kb.projections.research.assignment_projector import AssignmentProjector
from corpus_kb.projections.research.code_projector import CodeProjector
from corpus_kb.projections.research.exchange_projector import ExchangeProjector
from corpus_kb.projections.research.project_projector import ProjectProjector

logger = logging.getLogger(__name__)


class ResearchProjection:
    """One checkpoint, five bounded-context projectors."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        checkpoint: CheckpointManager,
        dlq: DLQHandler,
        embedder: ResearchEmbedder,
    ) -> None:
        self._pool = pool
        self._checkpoint = checkpoint
        self._dlq = dlq
        self._project = ProjectProjector(pool)
        self._exchange = ExchangeProjector(pool, embedder)
        self._code = CodeProjector(pool)
        self._assignment = AssignmentProjector(pool)
        self._analytics = AnalyticsProjector(pool)
        self._running = False

    async def process_notification(self, notification: Any) -> None:
        """Dispatch one decoded event through the staged projectors.

        Failures go to the DLQ; the checkpoint still advances so a poisoned
        event cannot wedge the subscription (retry is a re-run with the
        checkpoint reset, or a DLQ replay).
        """
        payload = event_payload(notification)
        try:
            tenant_id = require_tenant(payload, notification)
        except ValueError as exc:
            await self._fail(notification, notification.originator_id, exc)
            return
        try:
            await self._dispatch(notification, payload, tenant_id)
        except Exception as exc:
            logger.error("ResearchProjection failed on %s: %s", notification.topic, exc)
            await self._fail(notification, tenant_id, exc)

    async def _dispatch(self, notification: Any, payload: dict[str, Any], tenant_id: UUID) -> None:
        event_type = notification.event_type
        if event_type == "Document.Ingested":
            await self._project.on_ingested(notification)
            await self._exchange.on_ingested(notification)
        elif event_type == "Document.TurnsParsed":
            await self._exchange.on_turns_parsed(notification)
        elif event_type == "Document.ExchangesLinked":
            await self._exchange.on_exchanges_linked(notification)
        elif event_type == "CodebookVersion.Created":
            await self._code.on_created(notification)
        elif event_type == "CodebookVersion.CodeAdded":
            await self._code.on_codes_added(notification)
        elif event_type == "CodebookVersion.DefinitionRefined":
            await self._code.on_definition_refined(notification)
        elif event_type == "CodebookVersion.ThresholdRecalibrated":
            await self._code.on_thresholds(notification)
        elif event_type == "CodebookVersion.KeywordSetUpdated":
            await self._code.on_keywords(notification)
        elif event_type == "CodingAssignment.Recorded":
            await self._assignment.on_recorded(notification)
        elif event_type == "CodingAssignment.Reviewed":
            await self._assignment.on_reviewed(notification)
        elif event_type == "CodingAssignment.SignalRecorded":
            await self._analytics.on_signals(notification)
        elif event_type == "CodingRun.Started":
            await self._analytics.on_run_started(notification)
        elif event_type == "CodingRun.CheckpointComputed":
            await self._analytics.on_checkpoint(notification)
        elif event_type == "CodingRun.Stopped":
            await self._analytics.on_run_stopped(notification)
        else:
            logger.debug("ResearchProjection ignoring event %s", event_type)

    async def catch_up_once(self, reader: EventReader) -> int:
        """Drain the sequence once; returns the number of events processed."""
        cp = await self._checkpoint.get_checkpoint(RESEARCH_PROJECTION_NAME, DEFAULT_TENANT)
        last_sequence = int(cp["last_sequence"]) if cp and cp["last_sequence"] else 0
        notifications = await reader.read_since(last_sequence, limit=500)
        processed = 0
        for notification in notifications:
            await self.process_notification(notification)
            await self._checkpoint.update_checkpoint(
                RESEARCH_PROJECTION_NAME,
                DEFAULT_TENANT,
                deterministic_event_id(notification.originator_id, notification.originator_version),
                event_timestamp_dt(notification.event),
                last_sequence=notification.notification_id,
            )
            processed += 1
        return processed

    async def catch_up(self, reader: EventReader) -> int:
        """Drain until quiescent (bounded passes); total events processed."""
        total = 0
        for _ in range(20):
            processed = await self.catch_up_once(reader)
            total += processed
            if processed == 0:
                break
        return total

    async def run(self, reader: EventReader) -> None:
        """Background subscription loop (server mode)."""
        self._running = True
        logger.info("ResearchProjection started")
        while self._running:
            try:
                processed = await self.catch_up_once(reader)
                if processed == 0:
                    await asyncio.sleep(1.0)
            except Exception as exc:
                logger.error("ResearchProjection loop error: %s", exc)
                await asyncio.sleep(5.0)

    def stop(self) -> None:
        self._running = False

    async def _fail(self, notification: Any, tenant_id: Any, exc: Exception) -> None:
        await self._dlq.record_failure(
            RESEARCH_PROJECTION_NAME,
            UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id,
            deterministic_event_id(notification.originator_id, notification.originator_version),
            notification.event_type,
            str(exc),
        )

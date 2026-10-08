"""Regression: a mid-loop re-cluster must not grow ``assigned`` past the
observation count.

The pre-fix ``_grow`` reseeded ``assigned`` with full-length re-clustered
labels and then APPENDED the remaining streamed units, so any re-cluster
before the final batch grew ``assigned`` past ``len(observations)`` — the
next ``centroids_in_original_space`` call crashed with IndexError and
post-refresh units were double-counted into the centroids. ``due``
re-clusters fire every ``recluster_every_batches`` batches, so this triggers
on any corpus large enough to leave a batch after the first re-cluster.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import numpy
import pytest

from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research import inductive_run

TENANT = UUID(int=7)
RUN = UUID(int=8)


class _FakeHandler:
    def __init__(self) -> None:
        self.checkpoints: list[dict[str, Any]] = []

    def checkpoint_coding_run(
        self, tenant_id: object, run_id: object, payload: dict[str, Any]
    ) -> None:
        self.checkpoints.append(payload)


class _FakeConn:
    async def fetchrow(self, *args: object, **kwargs: object) -> None:
        return None


@asynccontextmanager
async def _fake_tenant_connection(pool: object, tenant_id: object):
    yield _FakeConn()


async def _record_signal(*args: object, **kwargs: object) -> None:
    return None


def _stub_cluster_embeddings(embeddings: object, **pins: object) -> SimpleNamespace:
    # Full-length deterministic labels: two halves, one label each.
    return SimpleNamespace(labels=[0] * 30 + [1] * 30)


def _observations(n: int) -> list[dict[str, Any]]:
    # Two well-separated 2-D groups so nearest-centroid routing is stable.
    points = [(0.0, 0.0)] * (n // 2) + [(1.0, 1.0)] * (n - n // 2)
    return [
        {"unit_id": 100 + i, "embedding": numpy.array(points[i], dtype=float)} for i in range(n)
    ]


async def test_grow_mid_loop_recluster_keeps_assigned_aligned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inductive_run, "tenant_connection", _fake_tenant_connection)
    monkeypatch.setattr(inductive_run, "record_signal", _record_signal)
    monkeypatch.setattr(inductive_run, "cluster_embeddings", _stub_cluster_embeddings)

    n = 60
    pilot = 10  # remaining = 50 -> two 25-unit batches; re-cluster after each
    handler = _FakeHandler()
    summary = await inductive_run._grow(
        pool=object(),
        handler=cast(ResearchHandler, handler),
        tenant_id=TENANT,
        run_id=RUN,
        observations=_observations(n),
        pilot_labels=[0] * pilot,
        centroids={0: [0.0, 0.0], 1: [1.0, 1.0]},
        code_names=["a", "b"],
        llm=None,
        settings={
            "entropy_threshold": 2.0,
            "recluster_every_batches": 1,
            "centroid_drift_threshold": 0.15,
        },
        pins={},
    )
    # Pre-fix this raises IndexError from centroids_in_original_space after
    # the second batch appends onto full-length reseeded labels.
    assert summary["batches"] == 2
    assert len(handler.checkpoints) == 2

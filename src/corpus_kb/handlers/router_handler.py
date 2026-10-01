"""Adaptive-RAG query router.

Classifies an incoming query by cosine similarity to per-intent prototype
centroids (no training) and dispatches to the backend suited to it:
semantic -> hybrid vector+FTS search, relational -> templated aggregate
stats, graph -> entity/relation traversal. Falls back to semantic search
on low confidence or an all-zero (degraded-embedder) query vector, since
misrouting to an empty/wrong backend is worse than defaulting to the
always-available hybrid search.
"""

from __future__ import annotations

import inspect
import logging
import math
from typing import cast

from corpus_kb.domain.models import RouteDecision, RoutedQuery, RoutedResult, SearchQuery

logger = logging.getLogger(__name__)

_ROUTE_ORDER = ["semantic", "relational", "graph"]


class RouterHandler:
    def __init__(
        self,
        query_handler,
        graph_handler,
        versioning_handler,
        embedder,
        config: dict[str, object],
    ) -> None:
        self._query = query_handler
        self._graph = graph_handler
        self._versioning = versioning_handler
        self._embedder = embedder
        routing_cfg = cast(dict[str, object], config.get("routing", {}) or {})
        self._threshold = float(routing_cfg.get("confidence_threshold", 0.35))
        prototypes = cast(dict[str, list[str]], routing_cfg.get("prototypes", {}) or {})
        self._prototypes: dict[str, list[str]] = {}
        for route in _ROUTE_ORDER:
            phrases = prototypes.get(route, [])
            if phrases:
                self._prototypes[route] = phrases
        self._centroids: dict[str, list[float]] = {}
        self._centroids_ready = False

    async def _embed(self, text: str) -> list[float]:
        if inspect.iscoroutinefunction(self._embedder.embed):
            return await self._embedder.embed(text)
        return self._embedder.embed(text)

    async def _ensure_centroids(self) -> None:
        if self._centroids_ready:
            return
        for route, phrases in self._prototypes.items():
            vectors = [await self._embed(p) for p in phrases]
            self._centroids[route] = _normalize(_mean(vectors))
        self._centroids_ready = True

    async def _classify(self, query: str) -> RouteDecision:
        vector = await self._embed(query)
        if all(v == 0.0 for v in vector):
            return RouteDecision(route="semantic", confidence=0.0, fallback=True)
        await self._ensure_centroids()
        normalized = _normalize(vector)
        best_route, best_score = "semantic", -1.0
        for route, centroid in self._centroids.items():
            score = _cosine(normalized, centroid)
            if score > best_score:
                best_route, best_score = route, score
        if best_score < self._threshold:
            return RouteDecision(route="semantic", confidence=best_score, fallback=True)
        return RouteDecision(route=best_route, confidence=best_score, fallback=False)

    async def handle_routed_query(self, query: RoutedQuery) -> RoutedResult:
        decision = await self._classify(query.query)
        if decision.route == "graph":
            hit = await self._graph.handle_search_graph(query.tenant_id, query.query, None, limit=1)
            if not hit:
                decision = RouteDecision(
                    route="semantic", confidence=decision.confidence, fallback=True
                )
            else:
                relations = await self._graph.handle_get_entity_relations(
                    query.tenant_id, hit[0]["entity_id"]
                )
                return RoutedResult(decision=decision, results=relations)
        if decision.route == "relational":
            stats = await self._versioning.handle_query_document_stats(query.tenant_id)
            return RoutedResult(decision=decision, results=[stats])
        # semantic (default and fallback path)
        hits = await self._query.handle_search(
            SearchQuery(tenant_id=query.tenant_id, query=query.query, k=query.k)
        )
        return RoutedResult(decision=decision, results=[h.model_dump(mode="json") for h in hits])


def _mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


_router_handler: RouterHandler | None = None


def get_router_handler() -> RouterHandler:
    if _router_handler is None:
        raise RuntimeError("RouterHandler not initialized")
    return _router_handler


def set_router_handler(handler: RouterHandler) -> None:
    global _router_handler
    _router_handler = handler


def reset_router_handler() -> None:
    global _router_handler
    _router_handler = None

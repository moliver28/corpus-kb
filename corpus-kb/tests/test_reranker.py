"""TDD tests for the reranker module."""

from __future__ import annotations

import pytest

from src.rag.reranker import FakeReranker, build_reranker


def test_fake_reranker_is_deterministic() -> None:
    r = FakeReranker()
    first = r.score("q", ["a", "b"])
    second = r.score("q", ["a", "b"])
    assert first == second
    assert len(first) == 2


def test_build_reranker_returns_none_when_disabled() -> None:
    cfg = {"search": {"rerank": {"enabled": False}}}
    assert build_reranker(cfg) is None


def test_build_reranker_returns_fake_in_test_mode() -> None:
    cfg = {"search": {"rerank": {"enabled": True, "backend": "fake"}}}
    reranker = build_reranker(cfg)
    assert reranker is not None
    assert reranker.score("q", ["x"]) == FakeReranker().score("q", ["x"])

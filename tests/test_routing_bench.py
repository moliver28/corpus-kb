"""Golden bench for the question router's phrase path (wave 2, Task 3).

Loads ``tests/fixtures/routing_golden.jsonl`` (60 rows, two per method
id 01-30) and runs every row through ``route_question`` with
``embedder=None`` — the deterministic phrase path, no Ollama. The gate
is twofold: the measured primary accuracy must stay at or above the
``routing_phrase_accuracy`` floor in ``scripts/quality-baselines.json``,
and a second full bench run must be byte-identical per row. The bench
functions are importable so the W5 embedding path and the regret metric
can reuse them unchanged.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from corpus_kb.rag.embedder import OllamaEmbedder

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.question_router import RoutingEmbedder, route_question
from corpus_kb.analysis.settings import (
    DEFAULT_ROUTING_CONFIDENCE_THRESHOLD,
    DEFAULT_ROUTING_MARGIN,
)
from tests.live_embed import live_embed_dimensions, live_embed_model

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "routing_golden.jsonl"
BASELINES_PATH = REPO_ROOT / "scripts" / "quality-baselines.json"

_BENCH_SETTINGS: dict[str, object] = {
    "confidence_threshold": DEFAULT_ROUTING_CONFIDENCE_THRESHOLD,
    "margin": DEFAULT_ROUTING_MARGIN,
}


@dataclass(frozen=True)
class GoldenRow:
    """One fixture row: the question and the acceptable methods."""

    question: str
    expected_method_ids: list[str]


@dataclass(frozen=True)
class BenchOutcome:
    """One routed row plus the hit bookkeeping the metrics read."""

    question: str
    expected_method_ids: list[str]
    chosen_method_id: str | None
    status: str
    confidence: float
    ranked_method_ids: list[str]

    @property
    def hit(self) -> bool:
        """The chosen method is one of the expected methods."""
        return self.chosen_method_id in self.expected_method_ids

    @property
    def primary_hit(self) -> bool:
        """The chosen method sits at position 0 of the expected list."""
        if not self.expected_method_ids:
            return False
        return self.chosen_method_id == self.expected_method_ids[0]

    def to_json_line(self) -> str:
        """The stable per-row line used for the byte-identity check."""
        payload = {
            "question": self.question,
            "expected_method_ids": self.expected_method_ids,
            "chosen_method_id": self.chosen_method_id,
            "status": self.status,
            "confidence": self.confidence,
            "ranked_method_ids": self.ranked_method_ids,
        }
        return json.dumps(payload, sort_keys=True)


def load_golden_rows(path: Path = GOLDEN_PATH) -> list[GoldenRow]:
    """Read the fixture; one JSON object per line, file order kept."""
    rows: list[GoldenRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        rows.append(
            GoldenRow(
                question=str(raw["question"]),
                expected_method_ids=[str(item) for item in raw["expected_method_ids"]],
            )
        )
    return rows


def run_bench(
    rows: list[GoldenRow],
    register: MethodRegister,
    embedder: RoutingEmbedder | None = None,
    settings: dict[str, object] | None = None,
) -> list[BenchOutcome]:
    """Route every row and return the outcomes in fixture order.

    ``embedder=None`` keeps the deterministic phrase path. W5 passes a
    deterministic embedder here for the embedding path and regret.
    """
    active_settings = _BENCH_SETTINGS if settings is None else settings
    outcomes: list[BenchOutcome] = []
    for row in rows:
        result = route_question(row.question, register, embedder, active_settings)
        outcomes.append(
            BenchOutcome(
                question=row.question,
                expected_method_ids=list(row.expected_method_ids),
                chosen_method_id=result.intent.method_id,
                status=result.intent.status,
                confidence=result.intent.confidence,
                ranked_method_ids=list(result.intent.ranked_method_ids),
            )
        )
    return outcomes


def primary_accuracy(outcomes: list[BenchOutcome]) -> float:
    """Share of rows whose chosen id sits at position 0 of expected."""
    if not outcomes:
        return 0.0
    hits = sum(1 for outcome in outcomes if outcome.primary_hit)
    return hits / len(outcomes)


def outcome_blob(outcomes: list[BenchOutcome]) -> str:
    """The whole bench as one stable string for the byte-identity check."""
    return "\n".join(outcome.to_json_line() for outcome in outcomes)


def read_floor(baselines_path: Path = BASELINES_PATH) -> float:
    """The recorded ``routing_phrase_accuracy`` floor."""
    data = json.loads(baselines_path.read_text(encoding="utf-8"))
    return float(data["routing_phrase_accuracy"])


def _register() -> MethodRegister:
    return load_method_register()


def test_fixture_covers_each_method_exactly_twice() -> None:
    """60 rows; every id 01-30 is expected[0] in exactly two rows; no dupes."""
    rows = load_golden_rows()
    assert len(rows) == 60
    primaries = [row.expected_method_ids[0] for row in rows]
    counts = Counter(primaries)
    expected_ids = {f"{index:02d}" for index in range(1, 31)}
    assert set(counts) == expected_ids
    assert all(count == 2 for count in counts.values())
    assert len({row.question for row in rows}) == 60
    for row in rows:
        assert row.question.strip()
        assert row.expected_method_ids
        assert len(set(row.expected_method_ids)) == len(row.expected_method_ids)


def test_phrase_path_accuracy_meets_the_recorded_floor() -> None:
    """Every-row accuracy must stay at or above the honest measured floor."""
    rows = load_golden_rows()
    outcomes = run_bench(rows, _register())
    accuracy = primary_accuracy(outcomes)
    assert accuracy >= read_floor(), f"phrase-path accuracy {accuracy} below the floor"


def test_bench_run_is_byte_identical_when_rerun() -> None:
    """Two full runs, each with a fresh register load: byte-identical output.

    A separate ``_register()`` per run pins load-time determinism (the
    YAML parse and the phrase material), not just route determinism.
    """
    rows = load_golden_rows()
    first = outcome_blob(run_bench(rows, _register()))
    second = outcome_blob(run_bench(rows, _register()))
    assert first == second
    assert len(first.splitlines()) == len(rows)


def test_every_row_resolves_to_its_primary_method() -> None:
    """The honest floor is perfect routing: chosen id == expected[0] everywhere."""
    rows = load_golden_rows()
    outcomes = run_bench(rows, _register())
    misses = [
        (outcome.question[:60], outcome.chosen_method_id, outcome.expected_method_ids[0])
        for outcome in outcomes
        if not outcome.primary_hit
    ]
    assert misses == []


# -- Routing W5: the regret metric and the embedding-path legs. The
#    phrase path keeps its floor (the canary); the embedding path gains
#    a regret ceiling, additive in quality-baselines.json. Live legs
#    skip when Ollama is down, so CI never fails on an outage.

OLLAMA_URL = "http://localhost:11434"


def _ollama_up() -> bool:
    import socket

    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


def _live_embedder() -> OllamaEmbedder:
    """The live embedder this leg pulled, derived never hardcoded.

    Zero-drift F3 follow-up: the legs pull the shipped default, so the
    name and width come from tests/live_embed. A stale hardcoded name
    404s against the pulled server.
    """
    from corpus_kb.rag.embedder import OllamaEmbedder

    return OllamaEmbedder(
        {
            "embedding": {
                "provider": "ollama",
                "model": live_embed_model(),
                "base_url": OLLAMA_URL,
                "batch_size": 8,
                "dimensions": live_embed_dimensions(),
            }
        }
    )


def rank_distance(outcome: BenchOutcome) -> int:
    """The chosen id's zero-based rank inside expected; a miss or an
    ambiguity costs ``len(expected)`` — worse than the worst in-list rank."""
    if outcome.chosen_method_id in outcome.expected_method_ids:
        return outcome.expected_method_ids.index(outcome.chosen_method_id)
    return len(outcome.expected_method_ids)


def regret_mean(outcomes: list[BenchOutcome]) -> float:
    """Mean rank distance over the rows; 0.0 is perfect routing."""
    if not outcomes:
        return 0.0
    return sum(rank_distance(outcome) for outcome in outcomes) / len(outcomes)


def read_regret_ceiling(baselines_path: Path = BASELINES_PATH) -> float:
    """The recorded ``routing_regret_mean`` ceiling."""
    data = json.loads(baselines_path.read_text(encoding="utf-8"))
    return float(data["routing_regret_mean"])


def test_phrase_path_regret_is_zero() -> None:
    """Perfect phrase routing means every chosen id ranks first: regret 0."""
    rows = load_golden_rows()
    outcomes = run_bench(rows, _register())
    assert regret_mean(outcomes) == 0.0


@pytest.mark.skipif(not _ollama_up(), reason="Ollama not reachable")
def test_embedding_path_regret_meets_the_recorded_ceiling() -> None:
    """The live embedding path must stay at or under the additive ceiling."""
    rows = load_golden_rows()
    outcomes = run_bench(rows, _register(), embedder=_live_embedder())
    measured = regret_mean(outcomes)
    ceiling = read_regret_ceiling()
    assert measured <= ceiling, f"embedding-path regret {measured} above the ceiling {ceiling}"


@pytest.mark.skipif(not _ollama_up(), reason="Ollama not reachable")
def test_embedding_path_run_is_byte_identical_when_rerun() -> None:
    """Two full embedding-mode runs, fresh register AND embedder: same bytes.

    A fresh embedder per run keeps the SHA256 cache out of the claim:
    determinism is measured end-to-end, not between cache hits.
    """
    rows = load_golden_rows()
    outcomes = run_bench(rows, _register(), embedder=_live_embedder())
    # A degraded status would mean the zero-vector fallback ran, not the
    # embedding path under test.
    assert {outcome.status for outcome in outcomes} <= {"resolved", "ambiguous"}
    first = outcome_blob(outcomes)
    second = outcome_blob(run_bench(rows, _register(), embedder=_live_embedder()))
    assert first == second

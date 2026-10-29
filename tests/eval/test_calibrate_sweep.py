"""The calibration sweep against a fake engine (hermetic: no DB, no Ollama).

The sweep scores a family of query-time recipes against one scratch
engine: the sample is ingested ONCE, then every candidate ranks cheaply
through its own overrides. P-values come from a patched ``_ranx_p_value``
(the ranx-absent path is forced through the sign test branch).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.eval.calibrate import run_calibration
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import ChunkEntry, LabelIndex
from corpus_kb.eval.scoring import IngestedDoc
from corpus_kb.profiles.calibrate_spec import CalibrateSpec
from corpus_kb.research.retrieval import _child_uuid

_TIGHT = {"retrieval.rrf_k": 10}
_WIDE = {"retrieval.rrf_k": 200}
_RERANK_ON = {"reranking.enabled": True}
_SPEC = CalibrateSpec(
    candidates=("baseline", "tight"),
    recipes={"tight": _TIGHT},
    baseline="baseline",
    sample="s.txt",
    golden="g.jsonl",
    k=2,
    min_queries=1,
)
_CFG: dict[str, object] = {
    "embedding": {"model": "fake-model", "dimensions": 8},
    "reranking": {"enabled": False},
}
_KNOWN = {"baseline", "tight", "alpha", "beta", "wide"}


def _recipe_key(overrides: dict[str, object] | None) -> str:
    return json.dumps(overrides or {}, sort_keys=True)


class FakeEngine:
    """Ranks each query from a table keyed by recipe; counts ingests."""

    def __init__(
        self,
        rankings: dict[str, dict[str, list[str]]],
        entries: tuple[ChunkEntry, ...] = (),
        degraded: bool = False,
    ) -> None:
        self.rankings = rankings
        self.entries = entries
        self.degraded = degraded
        self.closed = False
        self.ingest_calls = 0
        self.recipe_keys: list[str] = []

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc:
        self.ingest_calls += 1
        return IngestedDoc(doc_id=f"doc-{path.name}-{strategy}", degraded=self.degraded)

    async def label_index(self) -> LabelIndex:
        return LabelIndex(entries=self.entries)

    async def rank(
        self, query: str, k: int, overrides: dict[str, object] | None = None
    ) -> list[str]:
        key = _recipe_key(overrides)
        self.recipe_keys.append(key)
        table = self.rankings.get(key, {})
        return table.get(query, [])[:k]

    async def close(self) -> None:
        self.closed = True


def _factory(
    rankings: dict[str, dict[str, list[str]]],
    entries: tuple[ChunkEntry, ...] = (),
    degraded: bool = False,
    built: list[FakeEngine] | None = None,
):
    async def factory(candidate: str) -> FakeEngine:
        engine = FakeEngine(rankings, entries, degraded)
        if built is not None:
            built.append(engine)
        return engine

    return factory


def _unit_entry(unit_id: int, doc: str = "s.txt") -> ChunkEntry:
    return ChunkEntry(
        chunk_id=_child_uuid(f"unit:{unit_id}"),
        doc=doc,
        start=0,
        end=10,
        unit_ids=(unit_id,),
    )


_HIT = _child_uuid("unit:1")
_MISS = _child_uuid("unit:2")


def _golden(tmp_path: Path) -> tuple[list[GoldenRecord], str, Path]:
    from corpus_kb.eval.golden import golden_digest

    path = tmp_path / "g.jsonl"
    path.write_text('{"query": "q1", "relevant_units": [1]}\n', encoding="utf-8")
    records = [
        GoldenRecord(
            query="q1",
            relevant_chunk_ids=(),
            relevant_units=(1,),
        )
    ]
    return records, golden_digest(path), path


def _sample(tmp_path: Path) -> list[Path]:
    path = tmp_path / "s.txt"
    path.write_text("hello world\n", encoding="utf-8")
    return [path]


def _read_lock(lock_path: Path) -> dict[str, object]:
    return dict(json.loads(lock_path.read_text(encoding="utf-8")))


def _patch_p(monkeypatch: pytest.MonkeyPatch, table: dict[str, float]) -> None:
    def fake_p(
        query_scores: object, baseline: str, challenger: str, metric: str, k: int | None
    ) -> float | None:
        return table.get(challenger)

    monkeypatch.setattr("corpus_kb.eval.stats._ranx_p_value", fake_p)


async def test_multi_candidate_sweep_picks_significant_winner_and_writes_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    rankings = {
        "{}": {"q1": [_MISS, _HIT]},
        _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
    }
    lock_path = tmp_path / "starter.lock.json"
    built: list[FakeEngine] = []
    _patch_p(monkeypatch, {"tight": 0.01})
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(rankings, entries, built=built),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=lock_path,
    )
    assert result.status == "success"
    assert result.chosen == "tight"
    # ONE engine for the whole family. score_candidate asks for an ingest
    # once per member; the production engine's cache collapses those to a
    # single real ingest (pinned in tests/pipeline/test_calibrate_sweep_engine.py).
    assert len(built) == 1
    assert built[0].ingest_calls == 2
    # Every family member ranked through its own recipe overrides.
    assert _recipe_key(_TIGHT) in built[0].recipe_keys
    by_name = {row.candidate: row for row in result.candidates}
    assert set(by_name) == {"baseline", "tight"}
    assert by_name["tight"].mrr > by_name["baseline"].mrr
    lock = _read_lock(lock_path)
    assert lock["profile_sha256"] == "psha"
    assert lock["golden_sha256"] == digest
    assert lock["chosen"] == "tight"
    assert lock["outcome"] == "success"
    assert lock["baseline"] == "baseline"
    assert lock["alpha"] == 0.05
    assert lock["min_queries"] == 1
    assert lock["proposed_overrides"] == _TIGHT
    assert lock["reranker_active"] is False
    assert "date" in lock


async def test_reranker_recipe_marks_lock_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    spec = CalibrateSpec(
        candidates=("baseline", "tight"),
        recipes={"tight": _RERANK_ON},
        baseline="baseline",
        sample="s.txt",
        golden="g.jsonl",
        k=2,
        min_queries=1,
    )
    rankings = {
        "{}": {"q1": [_MISS, _HIT]},
        _recipe_key(_RERANK_ON): {"q1": [_HIT, _MISS]},
    }
    _patch_p(monkeypatch, {"tight": 0.01})
    lock_path = tmp_path / "starter.lock.json"
    result = await run_calibration(
        spec,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(rankings, entries),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=lock_path,
    )
    assert result.status == "success"
    lock = _read_lock(lock_path)
    # The winner's recipe re-enables reranking over the disabled config.
    assert lock["reranker_active"] is True
    assert lock["proposed_overrides"] == _RERANK_ON


async def test_sign_test_refusal_writes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    rankings = {
        "{}": {"q1": [_MISS, _HIT]},
        _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
    }
    lock_path = tmp_path / "starter.lock.json"
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(rankings, entries),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=lock_path,
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "sign test" in result.reason
    assert result.chosen is None
    assert not lock_path.exists()


async def test_no_significant_winner_writes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    rankings = {
        "{}": {"q1": [_MISS, _HIT]},
        _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
    }
    lock_path = tmp_path / "starter.lock.json"
    _patch_p(monkeypatch, {"tight": 0.4})
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(rankings, entries),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=lock_path,
    )
    assert result.status == "no_significant_winner"
    assert result.chosen is None
    assert result.summary == {}
    assert not lock_path.exists()
    # The scored family is still reported honestly.
    assert {row.candidate for row in result.candidates} == {"baseline", "tight"}


async def test_too_few_scored_queries_report_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    spec = CalibrateSpec(
        candidates=("baseline", "tight"),
        recipes={"tight": _TIGHT},
        baseline="baseline",
        sample="s.txt",
        golden="g.jsonl",
        k=2,
        min_queries=5,
    )
    _patch_p(monkeypatch, {"tight": 0.01})
    result = await run_calibration(
        spec,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(
            {"{}": {"q1": [_HIT]}, _recipe_key(_TIGHT): {"q1": [_HIT]}},
            (_unit_entry(1), _unit_entry(2)),
        ),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=tmp_path / "x.lock.json",
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "5" in result.reason


async def test_baseline_auto_included_as_family_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    spec = CalibrateSpec(
        candidates=("tight",),
        recipes={"tight": _TIGHT},
        baseline="baseline",
        sample="s.txt",
        golden="g.jsonl",
        k=2,
        min_queries=1,
    )
    _patch_p(monkeypatch, {"tight": 0.01})
    result = await run_calibration(
        spec,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(
            {
                "{}": {"q1": [_MISS, _HIT]},
                _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
            },
            entries,
        ),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
    )
    assert result.status == "success"
    assert {row.candidate for row in result.candidates} == {"baseline", "tight"}


async def test_winner_tiebreak_prefers_name_ascending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    spec = CalibrateSpec(
        candidates=("baseline", "beta", "alpha"),
        recipes={"beta": _WIDE, "alpha": _TIGHT},
        baseline="baseline",
        sample="s.txt",
        golden="g.jsonl",
        k=2,
        min_queries=1,
    )
    # Both challengers tie on every metric; only the name breaks the tie.
    _patch_p(monkeypatch, {"alpha": 0.01, "beta": 0.01})
    result = await run_calibration(
        spec,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory(
            {
                "{}": {"q1": [_MISS, _HIT]},
                _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
                _recipe_key(_WIDE): {"q1": [_HIT, _MISS]},
            },
            entries,
        ),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
    )
    assert result.status == "success"
    assert result.chosen == "alpha"


async def test_single_candidate_family_has_no_challenger(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    spec = CalibrateSpec(
        candidates=("baseline",),
        sample="s.txt",
        golden="g.jsonl",
        k=2,
        min_queries=1,
    )
    result = await run_calibration(
        spec,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"{}": {"q1": [_HIT]}}, (_unit_entry(1),)),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=tmp_path / "x.lock.json",
    )
    assert result.status == "no_significant_winner"
    assert not (tmp_path / "x.lock.json").exists()


async def test_family_capped_at_eight_candidates(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    names = [f"c{index}" for index in range(9)]
    spec = CalibrateSpec(
        candidates=tuple(names),
        baseline="c0",
        sample="s.txt",
        golden="g.jsonl",
        min_queries=1,
    )
    built: list[FakeEngine] = []
    with pytest.raises(ValidationError, match="8"):
        await run_calibration(
            spec,
            golden=records,
            golden_sha256=digest,
            sample_paths=_sample(tmp_path),
            engine_factory=_factory({"{}": {}}, built=built),
            known_candidates=set(names),
            cfg=_CFG,
            profile_name="starter",
            profile_sha256="psha",
        )
    assert built == []


async def test_degraded_embeddings_refuse_to_score(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"{}": {}}, degraded=True),
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        known_candidates=_KNOWN,
        lock_path=tmp_path / "x.lock.json",
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "degraded" in result.reason
    assert result.chosen is None and result.summary == {}


async def test_unmatched_labels_report_insufficient(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"{}": {}}, ()),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "no golden label" in result.reason


async def test_unknown_candidate_rejected_before_any_engine(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    built: list[FakeEngine] = []
    spec = CalibrateSpec(
        candidates=("default", "nope"),
        baseline="default",
        sample="s",
        golden="g",
        min_queries=1,
    )
    with pytest.raises(ValidationError, match="nope"):
        await run_calibration(
            spec,
            golden=records,
            golden_sha256=digest,
            sample_paths=_sample(tmp_path),
            engine_factory=_factory({"{}": {}}, built=built),
            known_candidates={"default"},
            cfg=_CFG,
            profile_name="starter",
            profile_sha256="psha",
        )
    assert built == []


async def test_lock_deterministic_apart_from_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, digest, _ = _golden(tmp_path)
    entries = (_unit_entry(1), _unit_entry(2))
    rankings = {
        "{}": {"q1": [_MISS, _HIT]},
        _recipe_key(_TIGHT): {"q1": [_HIT, _MISS]},
    }
    _patch_p(monkeypatch, {"tight": 0.01})
    first = tmp_path / "one.lock.json"
    second = tmp_path / "two.lock.json"
    for lock_path in (first, second):
        await run_calibration(
            _SPEC,
            golden=records,
            golden_sha256=digest,
            sample_paths=_sample(tmp_path),
            engine_factory=_factory(rankings, entries),
            cfg=_CFG,
            profile_name="starter",
            profile_sha256="psha",
            known_candidates=_KNOWN,
            lock_path=lock_path,
        )
    one, two = _read_lock(first), _read_lock(second)
    one.pop("date")
    two.pop("date")
    assert one == two


# -- Lock enforcement (eval/lockfile.py)


def _lock_file(tmp_path: Path, recorded: str) -> Path:
    import json as json_module

    lock = tmp_path / "starter.lock.json"
    lock.write_text(json_module.dumps({"profile_sha256": recorded}), encoding="utf-8")
    return lock


def test_check_profile_lock_ignores_missing_lock(tmp_path: Path) -> None:
    from corpus_kb.eval.lockfile import check_profile_lock

    profile = tmp_path / "starter.yaml"
    profile.write_text("name: starter\n", encoding="utf-8")
    check_profile_lock(profile, tmp_path / "absent.lock.json")


def test_check_profile_lock_passes_on_match(tmp_path: Path) -> None:
    from corpus_kb.eval.lockfile import check_profile_lock, profile_file_sha256

    profile = tmp_path / "starter.yaml"
    profile.write_text("name: starter\n", encoding="utf-8")
    check_profile_lock(profile, _lock_file(tmp_path, profile_file_sha256(profile)))


def test_check_profile_lock_fails_on_drift(tmp_path: Path) -> None:
    from corpus_kb.eval.lockfile import check_profile_lock

    profile = tmp_path / "starter.yaml"
    profile.write_text("name: starter\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="calibration lock"):
        check_profile_lock(profile, _lock_file(tmp_path, "stale-hash"))


def test_check_profile_lock_fails_on_unreadable_json(tmp_path: Path) -> None:
    from corpus_kb.eval.lockfile import check_profile_lock

    profile = tmp_path / "starter.yaml"
    profile.write_text("name: starter\n", encoding="utf-8")
    lock = tmp_path / "starter.lock.json"
    lock.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError, match="not readable JSON"):
        check_profile_lock(profile, lock)


def test_check_profile_lock_fails_on_missing_hash(tmp_path: Path) -> None:
    from corpus_kb.eval.lockfile import check_profile_lock

    profile = tmp_path / "starter.yaml"
    profile.write_text("name: starter\n", encoding="utf-8")
    lock = tmp_path / "starter.lock.json"
    lock.write_text(json.dumps({"chosen": "baseline"}), encoding="utf-8")
    with pytest.raises(ValidationError, match="profile_sha256"):
        check_profile_lock(profile, lock)

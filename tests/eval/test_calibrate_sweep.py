"""The calibration sweep against a fake engine (hermetic: no DB, no Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.eval.calibrate import IngestedDoc, run_calibration
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.labels import ChunkEntry, LabelIndex
from corpus_kb.profiles.loader import CalibrateSpec
from corpus_kb.research.retrieval import _child_uuid

_SPEC = CalibrateSpec(candidates=("baseline",), sample="s.txt", golden="g.jsonl", k=2)
_CFG: dict[str, object] = {"embedding": {"model": "fake-model", "dimensions": 8}}
_KNOWN = {"baseline"}


class FakeEngine:
    """Ranks each query from a scripted table; optional degraded mode."""

    def __init__(
        self,
        ranking: dict[str, list[str]],
        entries: tuple[ChunkEntry, ...] = (),
        degraded: bool = False,
    ) -> None:
        self.ranking = ranking
        self.entries = entries
        self.degraded = degraded
        self.closed = False

    async def ingest(self, path: Path, strategy: str) -> IngestedDoc:
        return IngestedDoc(doc_id=f"doc-{path.name}-{strategy}", degraded=self.degraded)

    async def label_index(self) -> LabelIndex:
        return LabelIndex(entries=self.entries)

    async def rank(self, query: str, k: int) -> list[str]:
        return self.ranking.get(query, [])[:k]

    async def close(self) -> None:
        self.closed = True


def _factory(
    ranking_by_candidate: dict[str, dict[str, list[str]]],
    entries: tuple[ChunkEntry, ...] = (),
    degraded: bool = False,
    built: list[str] | None = None,
):
    async def factory(candidate: str) -> FakeEngine:
        if built is not None:
            built.append(candidate)
        return FakeEngine(ranking_by_candidate[candidate], entries, degraded)

    return factory


def _unit_entry(unit_id: int, doc: str = "s.txt") -> ChunkEntry:
    return ChunkEntry(
        chunk_id=_child_uuid(f"unit:{unit_id}"),
        doc=doc,
        start=0,
        end=10,
        unit_ids=(unit_id,),
    )


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


async def test_sweep_scores_candidates_and_writes_lock(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    good = {"q1": [_child_uuid("unit:1")]}
    lock_path = tmp_path / "starter.lock.json"
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"baseline": good}, (_unit_entry(1),)),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        lock_path=lock_path,
    )
    assert result.status == "success"
    assert result.chosen == "baseline"
    by_name = {row.candidate: row for row in result.candidates}
    assert by_name["baseline"].recall_at_k == 1.0
    lock = _read_lock(lock_path)
    assert lock["profile_sha256"] == "psha"
    assert lock["golden_sha256"] == digest
    assert lock["chosen"] == "baseline"
    assert lock["rule"] == "span overlap >= 0.50 of the chunk length"
    sample_rows = lock["sample"]
    assert isinstance(sample_rows, list) and sample_rows[0]["path"] == "s.txt"
    assert "date" in lock


async def test_lock_deterministic_apart_from_date(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    ranking = {"q1": [_child_uuid("unit:1")]}
    entries = (_unit_entry(1),)
    first = tmp_path / "one.lock.json"
    second = tmp_path / "two.lock.json"
    await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"baseline": ranking}, entries),
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        known_candidates=_KNOWN,
        lock_path=first,
    )
    await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"baseline": ranking}, entries),
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
        known_candidates=_KNOWN,
        lock_path=second,
    )
    one, two = _read_lock(first), _read_lock(second)
    one.pop("date")
    two.pop("date")
    assert one == two


async def test_degraded_embeddings_refuse_to_score(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    result = await run_calibration(
        _SPEC,
        golden=records,
        golden_sha256=digest,
        sample_paths=_sample(tmp_path),
        engine_factory=_factory({"baseline": {}}, degraded=True),
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
        engine_factory=_factory({"baseline": {}}, ()),
        known_candidates=_KNOWN,
        cfg=_CFG,
        profile_name="starter",
        profile_sha256="psha",
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "no golden label" in result.reason


async def test_unknown_candidate_rejected_before_any_engine(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    built: list[str] = []
    spec = CalibrateSpec(candidates=("default", "nope"), sample="s", golden="g")
    with pytest.raises(ValidationError, match="nope"):
        await run_calibration(
            spec,
            golden=records,
            golden_sha256=digest,
            sample_paths=_sample(tmp_path),
            engine_factory=_factory({"default": {}, "nope": {}}, built=built),
            known_candidates={"default"},
            cfg=_CFG,
            profile_name="starter",
            profile_sha256="psha",
        )
    assert built == []


async def test_multi_candidate_sweep_refused(tmp_path: Path) -> None:
    records, digest, _ = _golden(tmp_path)
    spec = CalibrateSpec(candidates=("slow", "fast"), sample="s.txt", golden="g.jsonl", k=2)
    built: list[str] = []
    with pytest.raises(ValidationError, match="Multi-candidate"):
        await run_calibration(
            spec,
            golden=records,
            golden_sha256=digest,
            sample_paths=_sample(tmp_path),
            engine_factory=_factory({"slow": {}, "fast": {}}, built=built),
            known_candidates={"slow", "fast"},
            cfg=_CFG,
            profile_name="starter",
            profile_sha256="psha",
        )
    assert built == []


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

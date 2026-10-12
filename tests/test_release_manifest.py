"""ReleaseManifest (U2) — canonical JSON determinism and hash stability."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from corpus_kb.research.release.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ReleaseManifest,
    canonical_json_bytes,
    canonical_sha256,
    canonical_timestamp,
)


def _manifest(**overrides: object) -> ReleaseManifest:
    kwargs: dict[str, object] = {
        "release_id": "11111111-1111-1111-1111-111111111111",
        "codebook_id": "22222222-2222-2222-2222-222222222222",
        "codebook_version": "33333333-3333-3333-3333-333333333333",
        "codebook_sha256": "a" * 64,
        "profile": {"name": "team-codebook", "per_code_f1_floor": 0.7},
        "method_declaration": {"inductive": "hdbscan", "coding_unit": "speaker_turn"},
        "machine_profile": {
            "model": "qwen3:8b",
            "model_digest": "b" * 64,
            "embedder": "nomic-embed-text",
            "embedder_dimension": 1024,
            "decoding": {"temperature": 0.0},
            "seed": 42,
        },
        "created_at": "2026-10-09T00:00:00+00:00",
    }
    kwargs.update(overrides)
    return ReleaseManifest(**kwargs)  # pyright: test helper, fields validated at runtime


def test_identical_inputs_byte_identical_payload_and_hash():
    first = _manifest()
    second = _manifest()
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.sha256() == second.sha256()


def test_dict_ordering_does_not_leak_into_hash():
    ordered = _manifest(profile={"name": "team-codebook", "a": 1, "b": 2, "c": 3, "d": 4})
    shuffled = _manifest(profile={"d": 4, "c": 3, "b": 2, "a": 1, "name": "team-codebook"})
    assert ordered.canonical_bytes() == shuffled.canonical_bytes()
    assert ordered.sha256() == shuffled.sha256()


def test_float_formatting_and_precision_are_fixed():
    noisy = _manifest(metric_snapshots={"alpha": 0.6666666666666666, "f1": 0.70000000001})
    same = _manifest(metric_snapshots={"alpha": 0.6666667, "f1": 0.7})
    different = _manifest(metric_snapshots={"alpha": 0.667, "f1": 0.7})
    assert noisy.sha256() == same.sha256()
    assert noisy.sha256() != different.sha256()
    payload = json.loads(noisy.canonical_bytes().decode("utf-8"))
    assert payload["metric_snapshots"]["alpha"] == 0.666667


def test_minus_zero_and_nonfinite_floats():
    assert canonical_json_bytes({"x": -0.0, "y": 0.0}) == b'{"x":0.0,"y":0.0}'
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"x": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"x": float("inf")})


def test_timestamps_normalized_to_utc_iso():
    naive = datetime(2026, 10, 9, 12, 0, 0)
    shifted = datetime(2026, 10, 9, 14, 0, 0, tzinfo=UTC)
    assert canonical_timestamp(naive) == "2026-10-09T12:00:00+00:00"
    assert canonical_timestamp(shifted) == "2026-10-09T14:00:00+00:00"
    east = datetime(2026, 10, 9, 15, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonical_timestamp(east) == "2026-10-09T13:00:00+00:00"


def test_payload_carries_schema_version_and_timestamps():
    manifest = _manifest(released_at="2026-10-09T01:02:03+00:00")
    payload = manifest.to_payload()
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
    timestamps = payload["timestamps"]
    assert isinstance(timestamps, dict)
    assert timestamps["created_at"] == "2026-10-09T00:00:00+00:00"
    assert timestamps["released_at"] == "2026-10-09T01:02:03+00:00"


def test_codes_snapshot_changes_the_hash():
    base = _manifest()
    with_codes = _manifest(codes=[{"name": "billing", "definition": "d"}])
    assert base.sha256() != with_codes.sha256()


def test_canonical_sha256_matches_bytes_hash():
    payload = {"b": 1, "a": [1.5, 2.5]}
    import hashlib

    assert canonical_sha256(payload) == hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

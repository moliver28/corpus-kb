"""ReleaseManifest (U2, v6 §6) — the byte-stable, SHA-256-hashed lock payload.

CANONICAL JSON CONTRACT: sorted keys, ``(``,``)`` separators (no whitespace),
non-ASCII kept literal (UTF-8), every float rounded to 6 decimal places
(``-0.0`` normalized to ``0.0``, non-finite floats rejected), datetimes
normalized to UTC ISO-8601 with an explicit ``+00:00`` offset. The SHA-256 is
taken over those exact bytes. Identical inputs therefore always yield
byte-identical payloads and identical hashes — dict insertion order and
float formatting noise cannot leak in.

Manifest contents (v6 §6): schema version; release/codebook identity and
content hash; parent release; resolved profile; method declaration; partition
membership hashes and counts; sampling design; machine profile; metric
snapshots; gate results; waivers; approvers; timestamps; plus the U43
structured-output status and the U44 clustering-determinism proof.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

MANIFEST_SCHEMA_VERSION = "corpus-kb.release-manifest/1"
METRIC_PRECISION = 6


def canonical_timestamp(value: str | datetime) -> str:
    """UTC ISO-8601 string; naive datetimes are treated as UTC."""
    if isinstance(value, str):
        return value
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat()
    return dt.astimezone(UTC).isoformat()


def _canonicalize(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("manifest floats must be finite (never NaN/inf)")
        rounded = round(value, METRIC_PRECISION)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return str(value)


def canonical_json_bytes(payload: dict[str, object]) -> bytes:
    """The canonical serialization every hash in the release layer is over."""
    return json.dumps(
        _canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(payload: dict[str, object]) -> str:
    """SHA-256 over :func:`canonical_json_bytes`."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class ReleaseManifest:
    """Typed release manifest; hashing goes through the canonical bytes."""

    release_id: str
    codebook_id: str
    codebook_version: str
    codebook_sha256: str
    profile: dict[str, object]
    method_declaration: dict[str, object]
    machine_profile: dict[str, object]
    created_at: str
    codes: list[dict[str, object]] = field(default_factory=list)
    parent_release_id: str | None = None
    partition_memberships: dict[str, object] = field(default_factory=dict)
    sampling_design: dict[str, object] = field(default_factory=dict)
    metric_snapshots: dict[str, object] = field(default_factory=dict)
    gate_results: list[dict[str, object]] = field(default_factory=list)
    waivers: list[dict[str, object]] = field(default_factory=list)
    approvers: list[str] = field(default_factory=list)
    structured_output: dict[str, object] = field(default_factory=dict)
    clustering_determinism: dict[str, object] = field(default_factory=dict)
    consensus: dict[str, object] = field(default_factory=dict)
    released_at: str | None = None

    def to_payload(self) -> dict[str, object]:
        """Canonical-safe plain-dict view (order-free; hashing normalizes)."""
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "release_id": self.release_id,
            "parent_release_id": self.parent_release_id,
            "codebook_id": self.codebook_id,
            "codebook_version": self.codebook_version,
            "codebook_sha256": self.codebook_sha256,
            "profile": dict(self.profile),
            "method_declaration": dict(self.method_declaration),
            "machine_profile": dict(self.machine_profile),
            "codes": list(self.codes),
            "partition_memberships": dict(self.partition_memberships),
            "sampling_design": dict(self.sampling_design),
            "metric_snapshots": dict(self.metric_snapshots),
            "gate_results": list(self.gate_results),
            "waivers": list(self.waivers),
            "approvers": list(self.approvers),
            "structured_output": dict(self.structured_output),
            "clustering_determinism": dict(self.clustering_determinism),
            "consensus": dict(self.consensus),
            "timestamps": {
                "created_at": canonical_timestamp(self.created_at),
                "released_at": (
                    canonical_timestamp(self.released_at) if self.released_at else None
                ),
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_payload())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "METRIC_PRECISION",
    "ReleaseManifest",
    "canonical_json_bytes",
    "canonical_sha256",
    "canonical_timestamp",
]

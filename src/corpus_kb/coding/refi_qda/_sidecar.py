"""Manifest sidecar: build, name, and verify (U27, issue #77).

The REFI-QDA Project schema has no element that can hold a release manifest
(gate results, waivers, metric snapshots, approvers, the SHA-256 chain), so
the manifest travels VERBATIM next to the export as a canonical-JSON sidecar:

    {"manifest": <canonical manifest payload>,
     "manifest_sha256": <hex digest over exactly those bytes>,
     "schema_version": "corpus-kb.refi-qda-sidecar/1"}

The payload stored here is the manifest's *canonical* form (the bytes
``ReleaseManifest.canonical_bytes`` hashed) rather than the raw
``to_payload`` view: that way verification on import is a pure
re-canonicalization — ``canonical_json`` of the parsed sidecar reproduces
the carried bytes even for floats the canonical form rounds (e.g. ``1e-07``
to ``0.0``).

:func:`verify_sidecar` re-derives the canonical bytes and the manifest hash
on every import: a sidecar that was pretty-printed, edited, or paired with a
different project is refused. Release-gate evidence therefore survives the
round-trip with integrity asserted, not assumed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from corpus_kb.coding.refi_qda._codec import RefiQdaError, canonical_json, sha256_hex

SIDECAR_SCHEMA_VERSION = "corpus-kb.refi-qda-sidecar/1"
SIDECAR_SUFFIX = ".corpus-kb-manifest.json"


def sidecar_filename(xml_filename: str) -> str:
    """The sidecar name for an export file (written next to it)."""
    return f"{Path(xml_filename).stem}{SIDECAR_SUFFIX}"


def build_sidecar(canonical_payload: dict[str, object], manifest_sha256: str) -> str:
    """Canonical sidecar text for an already-canonical manifest payload."""
    return canonical_json(
        {
            "manifest": canonical_payload,
            "manifest_sha256": manifest_sha256,
            "schema_version": SIDECAR_SCHEMA_VERSION,
        }
    )


def verify_sidecar(
    sidecar_text: str, project_meta: dict[str, Any] | None
) -> tuple[dict[str, Any], str]:
    """Byte-identity + manifest-hash integrity (refuses anything altered).

    Returns ``(manifest, manifest_sha256)``. ``project_meta`` is the parsed
    Project-level meta (or None for a foreign document); when present, the
    manifest's ``codebook_id`` must match it.
    """
    sidecar = _parse_sidecar(sidecar_text)
    if canonical_json(sidecar) != sidecar_text:
        raise RefiQdaError("sidecar is not in canonical form (edited or pretty-printed?)")
    manifest = sidecar.get("manifest")
    if not isinstance(manifest, dict):
        raise RefiQdaError("sidecar has no manifest object")
    recorded = sidecar.get("manifest_sha256")
    actual = sha256_hex(canonical_json(manifest).encode("utf-8"))
    if not isinstance(recorded, str) or recorded != actual:
        raise RefiQdaError(
            f"sidecar manifest hash mismatch: recorded {recorded!r}, computed {actual!r}"
        )
    if project_meta is not None:
        bound = project_meta.get("codebook_id")
        if manifest.get("codebook_id") != bound:
            raise RefiQdaError(
                f"sidecar belongs to codebook {manifest.get('codebook_id')!r}, project to {bound!r}"
            )
    return manifest, recorded


def _parse_sidecar(sidecar_text: str) -> dict[str, Any]:
    try:
        sidecar = json.loads(sidecar_text)
    except ValueError as exc:
        raise RefiQdaError(f"sidecar is not valid JSON: {exc}") from exc
    if not isinstance(sidecar, dict):
        raise RefiQdaError("sidecar must be a JSON object")
    if sidecar.get("schema_version") != SIDECAR_SCHEMA_VERSION:
        raise RefiQdaError(
            f"sidecar schema_version {sidecar.get('schema_version')!r} "
            f"is not {SIDECAR_SCHEMA_VERSION!r}"
        )
    return sidecar

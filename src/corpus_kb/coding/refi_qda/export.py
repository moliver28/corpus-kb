"""Export a released codebook to REFI-QDA Project XML (U27, issue #77).

Produces the smallest standards-conformant slice of the REFI-QDA Project
schema (``urn:QDA-XML:project:1.0``, Project-mrt2019.xsd) that faithfully
round-trips a corpus-kb codebook: ``Project/CodeBook/Codes/Code`` (with the
standard's nested ``Code`` elements for hierarchies and ``Sets/Set`` with
``MemberCode`` refs for sets), plus the free-text ``Description`` payloads
that carry the fields the standard has no notation for (see ``_codec``).

The release manifest cannot be represented in the standard at all — no
element of the Project schema can hold gate results, waivers, metric
snapshots, or the manifest's SHA-256 chain. It therefore travels as a JSON
sidecar written next to the export (see ``_sidecar``); ``import_project``
refuses a sidecar whose canonical bytes or manifest hash do not verify, so
gate evidence survives a round-trip with integrity asserted, not assumed.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from corpus_kb.coding.codebook import validate_codebook
from corpus_kb.coding.refi_qda._codec import (
    META_MARKER,
    REFI_QDA_NAMESPACE,
    RefiQdaError,
    mint_guid,
    normalize_code_id,
    pack_description,
)
from corpus_kb.coding.refi_qda._sidecar import build_sidecar
from corpus_kb.research.release.manifest import ReleaseManifest

_ORIGIN = "corpus-kb REFI-QDA Project export (U27)"


@dataclass(frozen=True)
class RefiProjectExport:
    """The two artifacts of one export: standards XML plus manifest sidecar."""

    #: Serialized REFI-QDA Project XML (the content of a .qdpx's project.xml).
    project_xml: str
    #: Canonical JSON sidecar carrying the release manifest verbatim.
    sidecar_json: str
    #: SHA-256 over the manifest's canonical payload bytes.
    manifest_sha256: str


def _qualified(tag: str) -> str:
    return f"{{{REFI_QDA_NAMESPACE}}}{tag}"


def _project_meta(doc: dict[str, Any], manifest: ReleaseManifest) -> dict[str, object]:
    """Everything the XML tree cannot express, in the Project Description."""
    extra = {k: v for k, v in doc.items() if k not in ("paradigm", "categories", "sets")}
    return {
        "paradigm": doc["paradigm"],
        "codebook_id": manifest.codebook_id,
        "codebook_version": manifest.codebook_version,
        "sets": list(doc.get("sets") or []),
        "doc_extra": extra,
    }


def _code_tree(
    categories: list[dict[str, Any]],
) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], list[tuple[str, dict[str, Any]]]]:
    """Group raw code dicts by parent; reject dupes, dangling parents, cycles.

    Returns ``(children, roots)`` where children maps a parent code id to its
    ``(code_id, raw_code)`` entries in document order. The RAW dicts travel
    (never a ``code_id``-injected copy) so the emitted per-code metadata is
    identical to the source document under both the ``code_id`` and the
    legacy ``id`` key convention.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for code in categories:
        code_id = normalize_code_id(code)
        if code_id in by_id:
            raise RefiQdaError(f"duplicate code id {code_id!r} in codebook")
        by_id[code_id] = code
    children: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    roots: list[tuple[str, dict[str, Any]]] = []
    for code in categories:
        code_id = normalize_code_id(code)
        parent = code.get("parent_id")
        if parent is None or parent == "":
            roots.append((code_id, code))
            continue
        if not isinstance(parent, str):
            raise RefiQdaError(f"code {code_id!r} has a non-string parent_id")
        if parent not in by_id:
            raise RefiQdaError(f"code {code_id!r} references unknown parent {parent!r}")
        children.setdefault(parent, []).append((code_id, code))
    # Cycle check: every node must be reachable from a root exactly once.
    seen: set[str] = set()
    stack = [code_id for code_id, _ in roots]
    while stack:
        code_id = stack.pop()
        if code_id in seen:
            raise RefiQdaError(f"code hierarchy contains a cycle at {code_id!r}")
        seen.add(code_id)
        stack.extend(child_id for child_id, _ in children.get(code_id, []))
    if len(seen) != len(categories):
        unreachable = sorted(set(by_id) - seen)
        raise RefiQdaError(f"codes unreachable from any root (cycle?): {unreachable}")
    return children, roots


def _emit_code(
    parent_el: ET.Element[str],
    code_id: str,
    code: dict[str, Any],
    children: dict[str, list[tuple[str, dict[str, Any]]]],
    scope: str,
) -> None:
    echo = code.get("brief_definition")
    echo_text = echo if isinstance(echo, str) else ""
    if META_MARKER in echo_text:
        raise RefiQdaError(
            f"code {code_id!r} brief_definition contains the reserved meta marker; "
            "it would corrupt the Description payload"
        )
    if any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in echo_text):
        raise RefiQdaError(
            f"code {code_id!r} brief_definition contains XML-1.0-invalid control "
            "characters; they would serialize raw and fail XSD validation on import"
        )
    el = ET.SubElement(parent_el, _qualified("Code"))
    el.set("guid", mint_guid(scope, code_id))
    el.set("name", str(code.get("name", "")))
    el.set("isCodable", "true")
    desc = ET.SubElement(el, _qualified("Description"))
    desc.text = pack_description(echo_text, code)
    for child_id, child in children.get(code_id, []):
        _emit_code(el, child_id, child, children, scope)


def _emit_sets(root: ET.Element[str], sets: list[Any], scope: str, known_ids: set[str]) -> None:
    """Append the optional Sets block; ``known_ids`` are the codebook's code ids."""
    sets_el = ET.SubElement(root, _qualified("Sets"))
    for entry in sets:
        if not isinstance(entry, dict):
            raise RefiQdaError(f"set entry is not an object: {entry!r}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise RefiQdaError(f"set entry without a name: {entry!r}")
        code_ids = entry.get("code_ids", [])
        if not isinstance(code_ids, list):
            raise RefiQdaError(f"set {name!r} has a non-list code_ids")
        set_el = ET.SubElement(sets_el, _qualified("Set"))
        set_el.set("guid", mint_guid(scope, f"set:{name}"))
        set_el.set("name", name)
        description = entry.get("description")
        if isinstance(description, str) and description:
            desc = ET.SubElement(set_el, _qualified("Description"))
            desc.text = description
        for member in code_ids:
            if not isinstance(member, str) or member not in known_ids:
                raise RefiQdaError(f"set {name!r} references unknown code {member!r}")
            ref = ET.SubElement(set_el, _qualified("MemberCode"))
            ref.set("targetGUID", mint_guid(scope, member))


def export_project(
    doc: dict[str, Any],
    manifest: ReleaseManifest,
    *,
    project_name: str | None = None,
) -> RefiProjectExport:
    """Export a released codebook + manifest to REFI-QDA Project XML.

    Args:
        doc: The codebook document (``paradigm`` + ``categories``, optionally
            ``sets`` and extra top-level keys; all preserved verbatim).
        manifest: The release manifest; its canonical payload travels in the
            JSON sidecar and its identity anchors the exported guids.
        project_name: Optional Project ``name`` (defaults to the codebook id).

    Returns:
        A :class:`RefiProjectExport` with deterministic bytes: identical
        inputs always serialize to identical output.

    Raises:
        RefiQdaError: On an empty codebook (the REFI-QDA schema requires at
            least one ``Code``), duplicate or dangling code ids, cycles,
            malformed set entries, or a reserved marker in a definition.
    """
    validate_codebook(doc)
    categories: list[dict[str, Any]] = list(doc["categories"])
    if not categories:
        raise RefiQdaError(
            "cannot export an empty codebook: the REFI-QDA schema requires "
            "<Codes> to contain at least one <Code>"
        )
    scope = manifest.codebook_id
    children, roots = _code_tree(categories)

    root = ET.Element(_qualified("Project"))
    root.set("name", project_name if project_name is not None else f"corpus-kb {scope}")
    root.set("origin", _ORIGIN)
    timestamps = manifest.to_payload().get("timestamps")
    created_at = timestamps.get("created_at") if isinstance(timestamps, dict) else None
    if isinstance(created_at, str):
        root.set("creationDateTime", created_at)

    codebook_el = ET.SubElement(root, _qualified("CodeBook"))
    codes_el = ET.SubElement(codebook_el, _qualified("Codes"))
    for code_id, code in roots:
        _emit_code(codes_el, code_id, code, children, scope)

    sets_entries = doc.get("sets") or []
    if sets_entries:
        _emit_sets(root, sets_entries, scope, {normalize_code_id(c) for c in categories})

    project_desc = ET.SubElement(root, _qualified("Description"))
    project_desc.text = pack_description("", _project_meta(doc, manifest))

    # The sidecar carries the manifest's CANONICAL payload — exactly the bytes
    # ReleaseManifest.sha256 hashed — so import-side verification is a pure
    # re-canonicalization (json round-trips are byte-stable, including for
    # floats the canonical form rounds, e.g. 1e-07).
    canonical_payload: dict[str, object] = json.loads(manifest.canonical_bytes().decode("utf-8"))
    manifest_sha = manifest.sha256()

    # ElementTree's default_namespace option rejects unqualified attribute
    # names, so the default-prefix registration is used instead: idempotent,
    # scoped to our namespace, and it makes tostring emit the standard's
    # <Project xmlns="urn:QDA-XML:project:1.0" ...> form.
    ET.register_namespace("", REFI_QDA_NAMESPACE)
    xml_text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    )
    return RefiProjectExport(
        project_xml=xml_text,
        sidecar_json=build_sidecar(canonical_payload, manifest_sha),
        manifest_sha256=manifest_sha,
    )

"""Import REFI-QDA Project XML back into the codebook model (U27, issue #77).

The import understands exactly the slice :mod:`corpus_kb.coding.refi_qda.export`
writes — ``Project/CodeBook/Codes/Code`` (nested for hierarchies), the optional
``Sets`` block, and the marker-encoded ``Description`` payloads. It is honest
about the rest of the standard: Project sections it cannot map onto a pure
codebook model (``Users``/``Sources``/``Cases``/``Variables``/``Notes``/
``Links``/``Graphs``, or ``NoteRef`` pointers) are a loud ``RefiQdaError``,
never a silent drop. A code ``Description`` without the corpus-kb marker is a
foreign tool's plain text and imports with defaulted MacQueen fields.

When the manifest sidecar is supplied, its integrity is verified before the
result is returned (see ``_sidecar.verify_sidecar``): the sidecar bytes must
re-serialize to themselves under the canonical form, and the embedded
manifest payload must hash to the recorded ``manifest_sha256``. A sidecar
that was edited, pretty-printed, or re-paired with a different project is
rejected.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from corpus_kb.coding.codebook import validate_codebook
from corpus_kb.coding.refi_qda._codec import (
    REFI_QDA_NAMESPACE,
    RefiQdaError,
    normalize_code_id,
    unpack_description,
    valid_guid,
)
from corpus_kb.coding.refi_qda._sidecar import verify_sidecar

_ALLOWED_SECTIONS = frozenset(
    f"{{{REFI_QDA_NAMESPACE}}}{tag}" for tag in ("CodeBook", "Sets", "Description")
)
_XSD_BOOLEAN = {"true", "false", "1", "0"}


@dataclass(frozen=True)
class ImportedProject:
    """The codebook restored from a REFI-QDA Project, plus verified sidecar."""

    #: The codebook document (same shape the export consumed).
    codebook: dict[str, Any]
    #: Manifest payload from the sidecar; None when no sidecar was supplied.
    manifest: dict[str, Any] | None
    #: The sidecar's recorded manifest hash; None without a sidecar.
    manifest_sha256: str | None


def _qualified(tag: str) -> str:
    return f"{{{REFI_QDA_NAMESPACE}}}{tag}"


def _required_attr(el: ET.Element[str], name: str, where: str) -> str:
    value = el.get(name)
    if value is None:
        raise RefiQdaError(f"{where} is missing required attribute {name!r}")
    return value


def _element_text(parent: ET.Element[str], tag: str) -> str:
    """Text of the single child ``tag`` element ('' when absent)."""
    found = parent.findall(_qualified(tag))
    if len(found) > 1:
        raise RefiQdaError(f"<{tag}> may appear at most once")
    if not found or found[0].text is None:
        return ""
    return found[0].text


def _read_code(
    el: ET.Element[str], parent_id: str | None, codes: list[dict[str, Any]], ids: dict[str, str]
) -> None:
    where = "<Code>"
    guid = _required_attr(el, "guid", where)
    if not valid_guid(guid):
        raise RefiQdaError(f"{where} guid {guid!r} does not match the REFI-QDA GUID pattern")
    if guid in ids:
        raise RefiQdaError(f"duplicate code guid {guid!r} in project")
    name = _required_attr(el, "name", where)
    codable = _required_attr(el, "isCodable", where)
    if codable.lower() not in _XSD_BOOLEAN:
        raise RefiQdaError(f"{where} {name!r} has non-boolean isCodable {codable!r}")
    if el.find(_qualified("NoteRef")) is not None:
        raise RefiQdaError(f"code {name!r} carries a NoteRef; notes are not representable")

    echo, meta = unpack_description(_element_text(el, "Description"))
    if meta is not None:
        code: dict[str, Any] = dict(meta)
        if "code_id" not in code and "id" not in code:
            code["code_id"] = guid
    else:
        # Foreign export (no corpus-kb marker): keep the description verbatim
        # and default the MacQueen fields so the document still validates.
        code = {
            "code_id": guid,
            "name": name,
            "brief_definition": echo,
            "inclusion_criteria": "",
            "exclusion_criteria": "",
            "examples": [],
            "theory": None,
        }
    code["name"] = name
    if parent_id is not None:
        code.setdefault("parent_id", parent_id)

    code_id = normalize_code_id(code)
    if code_id in ids.values():
        raise RefiQdaError(f"duplicate code id {code_id!r} in project")
    ids[guid] = code_id
    codes.append(code)

    for child in el.findall(_qualified("Code")):
        _read_code(child, code_id, codes, ids)


def _read_xml_sets(root: ET.Element[str], ids: dict[str, str]) -> list[dict[str, Any]]:
    """Sets derived from the XML tree (the path for foreign documents)."""
    sets_el = root.find(_qualified("Sets"))
    if sets_el is None:
        return []
    entries: list[dict[str, Any]] = []
    for set_el in sets_el.findall(_qualified("Set")):
        guid = _required_attr(set_el, "guid", "<Set>")
        name = _required_attr(set_el, "name", "<Set>")
        if not valid_guid(guid):
            raise RefiQdaError(f"set {name!r} guid {guid!r} is not a REFI-QDA GUID")
        member_ids = []
        for ref in set_el.findall(_qualified("MemberCode")):
            target = _required_attr(ref, "targetGUID", f"<Set {name!r}> MemberCode")
            if target not in ids:
                raise RefiQdaError(f"set {name!r} references unknown code guid {target!r}")
            member_ids.append(ids[target])
        entry: dict[str, Any] = {"name": name, "code_ids": member_ids}
        description = _element_text(set_el, "Description")
        if description:
            entry["description"] = description
        entries.append(entry)
    return entries


def import_project(
    project_xml: str | bytes, sidecar_json: str | bytes | None = None
) -> ImportedProject:
    """Parse a REFI-QDA Project document back into the codebook model.

    Args:
        project_xml: Serialized Project XML (the project.xml content).
        sidecar_json: Optional canonical sidecar produced by the exporter;
            when given, its integrity and project binding are verified.

    Returns:
        An :class:`ImportedProject` whose ``codebook`` is content-equal to
        the document that was exported (``parent_id`` absence and an empty
        ``sets`` list both normalize to absent).

    Raises:
        RefiQdaError: On malformed XML, a wrong root element, sections the
            codebook model cannot represent, guid/boolean violations, or a
            sidecar failing canonical-form, hash, or binding verification.
    """
    try:
        root = ET.fromstring(project_xml)
    except ET.ParseError as exc:
        raise RefiQdaError(f"project XML is not well-formed: {exc}") from exc
    if root.tag != _qualified("Project"):
        raise RefiQdaError(
            f"root element is {root.tag!r}, expected {{{REFI_QDA_NAMESPACE}}}Project"
        )

    for child in root:
        if child.tag not in _ALLOWED_SECTIONS:
            local = child.tag.split("}")[-1]
            raise RefiQdaError(
                f"cannot import project section <{local}>: not representable "
                "in the codebook model (exported slices only)"
            )

    codes: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    codebook_el = root.find(_qualified("CodeBook"))
    if codebook_el is not None:
        codes_el = codebook_el.find(_qualified("Codes"))
        if codes_el is None:
            raise RefiQdaError("<CodeBook> is missing its <Codes> element")
        for code_el in codes_el.findall(_qualified("Code")):
            _read_code(code_el, None, codes, ids)

    xml_sets = _read_xml_sets(root, ids)

    _, project_meta = unpack_description(_element_text(root, "Description"))

    if project_meta is not None:
        doc: dict[str, Any] = {
            "paradigm": project_meta.get("paradigm", "codebook"),
            "categories": codes,
        }
        extra = project_meta.get("doc_extra")
        if isinstance(extra, dict):
            doc.update(extra)
        sets = project_meta.get("sets")
        if isinstance(sets, list) and sets:
            doc["sets"] = sets
    else:
        doc = {"paradigm": "codebook", "categories": codes}
        if xml_sets:
            doc["sets"] = xml_sets

    validate_codebook(doc)

    manifest: dict[str, Any] | None = None
    manifest_sha: str | None = None
    if sidecar_json is not None:
        sidecar_text = (
            sidecar_json.decode("utf-8") if isinstance(sidecar_json, bytes) else sidecar_json
        )
        manifest, manifest_sha = verify_sidecar(sidecar_text, project_meta)

    return ImportedProject(codebook=doc, manifest=manifest, manifest_sha256=manifest_sha)

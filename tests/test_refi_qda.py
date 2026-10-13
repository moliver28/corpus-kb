"""U27 (issue #77): REFI-QDA Project export/import round-trip tests.

The official XSD (``tests/fixtures/refi_qda/Project-mrt2019.xsd``) is applied
with ``lxml.etree.XMLSchema`` behind ``pytest.importorskip``: when lxml is
absent the XSD test skips cleanly and the structural assertions below keep
the suite green offline. No runtime code reads the XSD.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.coding.refi_qda import (
    GUID_PATTERN,
    REFI_QDA_NAMESPACE,
    SIDECAR_SCHEMA_VERSION,
    RefiQdaError,
    export_project,
    import_project,
    sidecar_filename,
)
from corpus_kb.coding.refi_qda._codec import META_MARKER, mint_guid
from corpus_kb.research.release.manifest import ReleaseManifest

XSD_PATH = Path(__file__).parent / "fixtures" / "refi_qda" / "Project-mrt2019.xsd"
NS = {"q": REFI_QDA_NAMESPACE}


def _code(code_id: str, name: str, definition: str, **extra: Any) -> dict[str, Any]:
    code: dict[str, Any] = {
        "code_id": code_id,
        "name": name,
        "brief_definition": definition,
        "inclusion_criteria": f"include when {name.lower()} appears",
        "exclusion_criteria": "metacommment only",
        "examples": [f"turn mentioning {name.lower()}"],
        "theory": {"tradition": "Grounded Theory", "construct": name, "citation": None},
    }
    code.update(extra)
    return code


def _codebook() -> dict[str, Any]:
    return {
        "paradigm": "codebook",
        "categories": [
            _code(
                "theme-experience",
                "Experience",
                "Participant reports lived experience of the workflow",
                parent_id=None,
            ),
            _code(
                "theme-friction",
                "Friction",
                "Participant describes friction, rework, or blockage",
                parent_id="theme-experience",
            ),
            _code(
                "theme-trust\u00e9",
                "Trust\u00e9",
                "Signals of trust or distrust in the tooling",
            ),
        ],
        "sets": [
            {
                "name": "negatives",
                "description": "codes used for adverse signal",
                "code_ids": ["theme-friction", "theme-trust\u00e9"],
            }
        ],
        "notes": "assembled by the U27 test factory",
    }


def _manifest() -> ReleaseManifest:
    # metric_snapshots deliberately carries floats the canonical form rounds
    # (0.30000000000000004 -> 0.3, 1e-07 -> 0.0): the sidecar must carry the
    # canonical payload or the round-trip hash check would fail on its own
    # export (regression pin for the canonicalization asymmetry).
    return ReleaseManifest(
        release_id="rel-001",
        codebook_id="cb-test-001",
        codebook_version="v3",
        codebook_sha256="ab" * 32,
        profile={"tier": "standard"},
        method_declaration={"method": "deductive"},
        machine_profile={"os": "test"},
        created_at="2026-10-12T00:00:00+00:00",
        metric_snapshots={"kappa": 0.30000000000000004, "epsilon": 1e-07},
        gate_results=[{"gate": "reliability", "passed": True}],
        approvers=["Ada"],
    )


@pytest.fixture()
def codebook() -> dict[str, Any]:
    return _codebook()


@pytest.fixture()
def manifest() -> ReleaseManifest:
    return _manifest()


@pytest.fixture()
def export(codebook: dict[str, Any], manifest: ReleaseManifest):
    return export_project(codebook, manifest)


# --- export structure (always runs; the offline stand-in for XSD validation) ---


def test_export_xml_structure(export) -> None:
    root = ET.fromstring(export.project_xml)
    assert root.tag == f"{{{REFI_QDA_NAMESPACE}}}Project"
    assert root.get("name") == "corpus-kb cb-test-001"
    assert root.get("origin")
    assert root.get("creationDateTime") == "2026-10-12T00:00:00+00:00"
    # XSD ProjectType sequence order: CodeBook, then Sets, then Description.
    assert [child.tag.split("}")[-1] for child in root] == ["CodeBook", "Sets", "Description"]

    top_codes = root.findall("q:CodeBook/q:Codes/q:Code", NS)
    assert [c.get("name") for c in top_codes] == ["Experience", "Trust\u00e9"]
    children = top_codes[0].findall("q:Code", NS)
    assert [c.get("name") for c in children] == ["Friction"]  # hierarchy by nesting

    for el in root.findall(".//q:Code", NS) + root.findall(".//q:Set", NS):
        assert GUID_PATTERN.match(el.get("guid", "")), el.get("name")
    for el in root.findall(".//q:Code", NS):
        assert el.get("isCodable") == "true"

    member = root.find(".//q:MemberCode", NS)
    assert member is not None
    assert member.get("targetGUID") == mint_guid("cb-test-001", "theme-friction")


def test_export_description_carries_marker_and_echo(export) -> None:
    root = ET.fromstring(export.project_xml)
    desc = root.find("q:CodeBook/q:Codes/q:Code/q:Description", NS)
    assert desc is not None and desc.text
    echo, marker, payload = desc.text.partition(META_MARKER)
    assert marker and echo.startswith("Participant reports lived experience")
    meta = json.loads(payload)
    assert meta["name"] == "Experience"
    assert meta["code_id"] == "theme-experience"


def test_export_is_deterministic(codebook: dict[str, Any], manifest: ReleaseManifest) -> None:
    first = export_project(codebook, manifest)
    second = export_project(codebook, manifest)
    assert first.project_xml == second.project_xml
    assert first.sidecar_json == second.sidecar_json
    assert first.manifest_sha256 == second.manifest_sha256 == manifest.sha256()


def test_sidecar_filename_naming() -> None:
    assert sidecar_filename("release.qdpx") == "release.corpus-kb-manifest.json"


# --- XSD validation (lxml-gated) ---


def test_export_validates_against_official_xsd(export) -> None:
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(XSD_PATH)))
    document = lxml_etree.fromstring(export.project_xml.encode("utf-8"))
    assert schema.validate(document), str(schema.error_log)


def test_xsd_fixture_matches_provenance() -> None:
    provenance = (XSD_PATH.parent / "PROVENANCE.md").read_text(encoding="utf-8")
    recorded = re.search(r"[0-9a-f]{64}", provenance)
    assert recorded is not None, "PROVENANCE.md lost its SHA-256 line"
    # Provenance pins the committed LF bytes; autocrlf checkouts translate
    # them to CRLF on Windows, so hash the normalized form.
    data = XSD_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert recorded.group(0) == hashlib.sha256(data).hexdigest()


# --- round-trip ---


def test_round_trip_codebook_and_sidecar_integrity(
    codebook: dict[str, Any], manifest: ReleaseManifest, export
) -> None:
    result = import_project(export.project_xml, export.sidecar_json)

    assert result.codebook == codebook
    assert result.manifest_sha256 == manifest.sha256()
    # The manifest travels verbatim in its canonical form (the rounded floats
    # ARE the canonical values) and re-hashes to the recorded digest.
    assert result.manifest == json.loads(manifest.canonical_bytes().decode("utf-8"))
    assert result.manifest is not None
    snapshots = result.manifest["metric_snapshots"]
    assert snapshots["kappa"] == 0.3 and snapshots["epsilon"] == 0.0
    # Importing twice is deterministic.
    again = import_project(export.project_xml, export.sidecar_json)
    assert again.codebook == result.codebook
    assert again.manifest_sha256 == result.manifest_sha256


def test_round_trip_without_sidecar(export, codebook: dict[str, Any]) -> None:
    result = import_project(export.project_xml)
    assert result.codebook == codebook
    assert result.manifest is None
    assert result.manifest_sha256 is None


def test_round_trip_legacy_id_keys(manifest: ReleaseManifest) -> None:
    doc = _codebook()
    doc["categories"] = [
        _code("legacy-root", "Legacy", "root under the legacy id key"),
        _code("legacy-child", "Child", "child under the legacy id key", parent_id=None),
    ]
    for code in doc["categories"]:
        code["id"] = code.pop("code_id")
    doc["categories"][1]["parent_id"] = "legacy-root"
    doc["sets"] = [{"name": "all", "code_ids": ["legacy-root", "legacy-child"]}]

    export = export_project(doc, manifest)
    result = import_project(export.project_xml, export.sidecar_json)
    assert result.codebook == doc  # no injected code_id key, content exact


def test_round_trip_unicode_survives(manifest: ReleaseManifest) -> None:
    doc = _codebook()
    doc["categories"] = [
        _code("unicode", "Th\u00e9orie \u2014 th\u00e8se", "caf\u00e9 \u4fe1\u53f7")
    ]
    doc["sets"] = [{"name": "\u00e9tudes", "code_ids": ["unicode"]}]
    export = export_project(doc, manifest)
    assert "Th\u00e9orie \u2014 th\u00e8se" in export.project_xml
    assert import_project(export.project_xml, export.sidecar_json).codebook == doc


def test_foreign_project_import_defaults_macqueen_fields() -> None:
    guid_a, guid_b = mint_guid("foreign", "a"), mint_guid("foreign", "b")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Project xmlns="{REFI_QDA_NAMESPACE}" name="foreign">'
        "<CodeBook><Codes>"
        f'<Code guid="{guid_a}" name="Rival" isCodable="true">'
        "<Description>plain text from another tool</Description>"
        f'<Code guid="{guid_b}" name="Nest" isCodable="1"/></Code>'
        "</Codes></CodeBook>"
        f'<Sets><Set guid="{mint_guid("foreign", "s")}" name="pack">'
        f'<MemberCode targetGUID="{guid_b}"/></Set></Sets>'
        "</Project>"
    )
    result = import_project(xml)
    assert result.manifest is None
    doc = result.codebook
    rival, nest = doc["categories"]
    assert rival["code_id"] == guid_a and nest["code_id"] == guid_b
    assert rival["brief_definition"] == "plain text from another tool"
    for field in ("inclusion_criteria", "exclusion_criteria"):
        assert rival[field] == ""
    assert rival["examples"] == [] and rival["theory"] is None
    assert nest["parent_id"] == guid_a
    assert doc["sets"] == [{"name": "pack", "code_ids": [guid_b]}]


# --- honest rejection (negative tests) ---


def test_malformed_xml_rejected_honestly() -> None:
    with pytest.raises(RefiQdaError, match="not well-formed"):
        import_project("<this is not xml")
    export = export_project(_codebook(), _manifest())
    with pytest.raises(RefiQdaError, match="not well-formed"):
        import_project(export.project_xml[: len(export.project_xml) // 2])
    with pytest.raises(RefiQdaError, match="root element"):
        import_project('<Project name="no-namespace"/>')
    with pytest.raises(RefiQdaError, match="root element"):
        import_project("<html><body/></html>")


def test_unrepresentable_sections_rejected() -> None:
    guid = mint_guid("x", "a")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Project xmlns="{REFI_QDA_NAMESPACE}" name="x">'
        f'<CodeBook><Codes><Code guid="{guid}" name="a" isCodable="true"/></Codes></CodeBook>'
        '<Sources><TextSource guid="{guid2}"/></Sources>'
        "</Project>"
    )
    with pytest.raises(RefiQdaError, match="Sources"):
        import_project(body)
    with_note = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Project xmlns="{REFI_QDA_NAMESPACE}" name="x">'
        f'<CodeBook><Codes><Code guid="{guid}" name="a" isCodable="true">'
        f'<NoteRef targetGUID="{mint_guid("x", "n")}"/></Code></Codes></CodeBook>'
        "</Project>"
    )
    with pytest.raises(RefiQdaError, match="NoteRef"):
        import_project(with_note)
    foreign_ns = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Project xmlns="{REFI_QDA_NAMESPACE}" name="x">'
        '<evil xmlns="urn:evil">nope</evil></Project>'
    )
    with pytest.raises(RefiQdaError, match="evil"):
        import_project(foreign_ns)


def test_attribute_violations_rejected() -> None:
    def project(code_inner: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Project xmlns="{REFI_QDA_NAMESPACE}" name="x">'
            f"<CodeBook><Codes>{code_inner}</Codes></CodeBook></Project>"
        )

    with pytest.raises(RefiQdaError, match="GUID pattern"):
        import_project(project('<Code guid="nope" name="a" isCodable="true"/>'))
    with pytest.raises(RefiQdaError, match="isCodable"):
        import_project(project(f'<Code guid="{mint_guid("x", "a")}" name="a" isCodable="maybe"/>'))
    with pytest.raises(RefiQdaError, match="guid"):
        import_project(project('<Code name="a" isCodable="true"/>'))


def test_export_rejects_broken_codebooks(manifest: ReleaseManifest) -> None:
    empty = {"paradigm": "codebook", "categories": []}
    with pytest.raises(RefiQdaError, match="empty codebook"):
        export_project(empty, manifest)

    dangling = {"paradigm": "codebook", "categories": [_code("a", "A", "d", parent_id="ghost")]}
    with pytest.raises(RefiQdaError, match="unknown parent"):
        export_project(dangling, manifest)

    cyclic = {
        "paradigm": "codebook",
        "categories": [
            _code("a", "A", "d", parent_id="b"),
            _code("b", "B", "d", parent_id="a"),
        ],
    }
    with pytest.raises(RefiQdaError, match="cycle"):
        export_project(cyclic, manifest)

    dupe = {
        "paradigm": "codebook",
        "categories": [_code("a", "A", "d"), _code("a", "A2", "d2")],
    }
    with pytest.raises(RefiQdaError, match="duplicate code id"):
        export_project(dupe, manifest)

    marker = {"paradigm": "codebook", "categories": [_code("a", "A", f"fine {META_MARKER} json")]}
    with pytest.raises(RefiQdaError, match="meta marker"):
        export_project(marker, manifest)


def test_export_rejects_broken_sets(manifest: ReleaseManifest) -> None:
    doc = _codebook()
    doc["sets"] = [{"name": "bad", "code_ids": ["ghost"]}]
    with pytest.raises(RefiQdaError, match="unknown code"):
        export_project(doc, manifest)
    doc["sets"] = ["not-an-object"]
    with pytest.raises(RefiQdaError, match="not an object"):
        export_project(doc, manifest)
    doc["sets"] = [{"name": "bad", "code_ids": "theme-friction"}]
    with pytest.raises(RefiQdaError, match="non-list code_ids"):
        export_project(doc, manifest)


# --- sidecar integrity ---


def test_sidecar_pretty_printed_or_edited_is_rejected(export) -> None:
    pretty = json.dumps(json.loads(export.sidecar_json), indent=2, sort_keys=True)
    with pytest.raises(RefiQdaError, match="canonical form"):
        import_project(export.project_xml, pretty)

    tampered = json.loads(export.sidecar_json)
    tampered["manifest"]["approvers"] = ["Mallory"]
    recanonical = json.dumps(tampered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with pytest.raises(RefiQdaError, match="hash mismatch"):
        import_project(export.project_xml, recanonical)

    with pytest.raises(RefiQdaError, match="not valid JSON"):
        import_project(export.project_xml, "{not json")

    wrong_version = json.loads(export.sidecar_json)
    wrong_version["schema_version"] = "corpus-kb.refi-qda-sidecar/999"
    canonical = json.dumps(wrong_version, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with pytest.raises(RefiQdaError, match="schema_version"):
        import_project(export.project_xml, canonical)


def test_sidecar_repaired_canonical_form_still_hash_bound(export) -> None:
    """A re-canonicalized sidecar passes the byte check but not the hash check."""
    sidecar = json.loads(export.sidecar_json)
    sidecar["manifest"]["codebook_version"] = "v999"
    text = json.dumps(sidecar, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with pytest.raises(RefiQdaError, match="hash mismatch"):
        import_project(export.project_xml, text)


def test_sidecar_from_another_project_is_rejected(export) -> None:
    other = export_project(_codebook(), _manifest())  # the matching pair, for the sanity assert
    foreign = export_project(
        _codebook(),
        ReleaseManifest(
            release_id="rel-002",
            codebook_id="cb-other",
            codebook_version="v1",
            codebook_sha256="cd" * 32,
            profile={},
            method_declaration={},
            machine_profile={},
            created_at="2026-10-12T00:00:00+00:00",
        ),
    )
    with pytest.raises(RefiQdaError, match="belongs to codebook"):
        import_project(export.project_xml, foreign.sidecar_json)
    # sanity: a matching pair never trips the binding check
    assert import_project(other.project_xml, other.sidecar_json).manifest is not None


def test_sidecar_schema_version_constant() -> None:
    assert SIDECAR_SCHEMA_VERSION == "corpus-kb.refi-qda-sidecar/1"

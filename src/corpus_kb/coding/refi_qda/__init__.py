"""REFI-QDA Project export/import for released codebooks (U27, issue #77).

Bridge between the corpus-kb codebook/release model and the REFI-QDA Project
exchange standard (QDA-XML 1.0, ``urn:QDA-XML:project:1.0``; the official
``Project-mrt2019.xsd`` ships as a test fixture with full provenance in
``tests/fixtures/refi_qda/PROVENANCE.md``). No runtime dependencies: the XML
is built and parsed with the stdlib ``xml.etree.ElementTree``; the XSD itself
is only exercised in tests via ``lxml.etree.XMLSchema`` (dev extra, guarded
by ``pytest.importorskip``).

WHAT THE STANDARD CAN REPRESENT (and we emit):

- ``Project/CodeBook/Codes/Code`` — codes with ``guid``/``name``/``isCodable``;
  the standard represents hierarchies by NESTING ``Code`` elements, so a
  code's ``parent_id`` becomes its XML parent.
- ``Project/Sets/Set/MemberCode`` — named sets of codes as guid references.
- ``Description`` — free text, used for the human-readable brief definition.

WHAT THE STANDARD CANNOT REPRESENT (why there is a sidecar):

- A code's working fields beyond name/description: inclusion/exclusion
  criteria, examples, theory grounding, and every other per-code key travel
  in a marker-encoded JSON block inside the code's ``Description``
  (``_codec.META_MARKER``). Other QDA tools importing our file simply see a
  readable description; our import restores the full payload.
- A release manifest — gate results, waivers, metric snapshots, approvers,
  and the SHA-256 chain have no home anywhere in the Project schema. The
  manifest therefore travels VERBATIM as a canonical-JSON sidecar written
  next to the export (``<name>.corpus-kb-manifest.json``);
  :func:`import_project` re-verifies its hash and project binding on every
  round-trip, so release-gate evidence survives with integrity asserted.

Round-trip contract: ``import_project(export.project_xml,
export.sidecar_json).codebook`` is content-equal to the exported document
(``parent_id`` absence normalizes to absent; ``sets: []`` likewise), the
sidecar bytes are byte-identical under the canonical form, and importing
twice is deterministic. Guids are minted with UUIDv5 over
``{codebook_id}:{code_id}``, so exports are byte-stable across runs.

Public API::

    export = export_project(codebook_doc, manifest)
    result = import_project(export.project_xml, export.sidecar_json)
"""

from __future__ import annotations

from corpus_kb.coding.refi_qda._codec import (
    GUID_PATTERN,
    REFI_QDA_NAMESPACE,
    RefiQdaError,
)
from corpus_kb.coding.refi_qda._sidecar import (
    SIDECAR_SCHEMA_VERSION,
    SIDECAR_SUFFIX,
    sidecar_filename,
)
from corpus_kb.coding.refi_qda.export import RefiProjectExport, export_project
from corpus_kb.coding.refi_qda.importer import ImportedProject, import_project

__all__ = [
    "GUID_PATTERN",
    "REFI_QDA_NAMESPACE",
    "SIDECAR_SCHEMA_VERSION",
    "SIDECAR_SUFFIX",
    "ImportedProject",
    "RefiProjectExport",
    "RefiQdaError",
    "export_project",
    "import_project",
    "sidecar_filename",
]

"""The multipart upload operation row (owner ruling 4a, 2026-11-12).

One row: the browser upload front door. The route spools the form file and
calls the SAME ``ingest_file`` tool the file/CLI surfaces use, so the
receipt is the ``ingest_file`` envelope. Multipart form upload is an HTTP
shape by nature, so the row declares an HTTP-ONLY surface set — no
cli/mcp/socket surfaces — instead of wrapping the pipeline in a JSON
sibling (ruling 4a; the registry row ``ingest-upload`` mirrors this).

All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.catalogue import _TENANT
from corpus_kb.contracts.catalogue_ingest import _INGEST_EXTRA
from corpus_kb.contracts.envelopes import INGEST_ENVELOPE
from corpus_kb.contracts.model import HTTP, Operation, ParamSpec, SurfaceSpec

UPLOAD_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="ingest_upload",
        family="ingest",
        description=(
            "Upload one file by multipart form (field name file) and ingest "
            "it through the standard file pipeline."
        ),
        handler="corpus_kb.tools.ingest_upload_tools:ingest_upload",
        params=(ParamSpec("source_type"), _TENANT),
        envelope_keys=INGEST_ENVELOPE,
        http_extra=_INGEST_EXTRA,
        multipart=True,
        surfaces=(SurfaceSpec(HTTP, "/api/ingest/upload", "POST"),),
    ),
)

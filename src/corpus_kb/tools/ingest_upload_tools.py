"""Upload ingest handler (UI SPA program Task 5a, B3).

ONE handler for ``POST /api/ingest/upload``: sanitize the client filename,
spool the upload into a temp file under the configured size cap, and hand
the temp path to the SAME ``ingest_file`` tool the file/CLI surfaces use.
The HTTP result is the ``ingest_file`` envelope, unchanged.

Caps (plain numbers, one policy source):

- ``ui.spa.max_upload_mb`` (config default ``SPA_MAX_UPLOAD_MB_DEFAULT``)
  bounds every upload. The route enforces it against the ``Content-Length``
  header BEFORE the body is read; the stream loop here re-checks every
  chunk, so a missing or lying header cannot sneak bytes past the cap.
- The request must be ``multipart/form-data``; anything else is a typed
  415 through the taxonomy (``errors.py``), never a bare 400.

The multipart parser ships in the ``[ui]`` extra (``python-multipart``,
Apache-2.0). Without it the route answers 503 ``dependency_missing`` with
the install command.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

import asyncpg

from corpus_kb.config import SPA_MAX_UPLOAD_MB_DEFAULT
from corpus_kb.errors import (
    DependencyMissingError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass
from corpus_kb.tools.ingest_tools import ingest_file

__all__ = [
    "CHUNK_BYTES",
    "UploadSource",
    "enforce_request_caps",
    "ingest_upload",
    "require_multipart_support",
    "sanitize_filename",
    "upload_cap_mb",
]

# 1 MiB read/spool steps: bounded memory no matter how big the body claims
# to be, and cap checks every step of the stream.
CHUNK_BYTES = 1024 * 1024

_BYTES_PER_MB = 1024 * 1024


class UploadSource(Protocol):
    """The read side of one uploaded file (the starlette UploadFile shape)."""

    filename: str | None
    content_type: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...


def upload_cap_mb(config: dict[str, object] | None = None) -> int:
    """The per-file upload cap in MB (``ui.spa.max_upload_mb``)."""
    cfg = load_config_or_pass(config)
    ui = cfg.get("ui")
    spa = ui.get("spa") if isinstance(ui, dict) else None
    raw = spa.get("max_upload_mb") if isinstance(spa, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return SPA_MAX_UPLOAD_MB_DEFAULT
    return raw


def sanitize_filename(raw: str | None) -> str:
    """The basename of the client filename; junk names are a typed 400."""
    name = Path(raw or "").name.strip()
    if not name or name in {".", ".."}:
        raise ValidationError("The upload needs a file with a real name.")
    return name


def require_multipart_support() -> None:
    """Name the missing extra instead of letting the parser crash."""
    if importlib.util.find_spec("python_multipart") is None:
        raise DependencyMissingError(
            "The upload route needs the multipart form parser.",
            remediation=(
                "Install the ui extra: pip install '.[ui]'. "
                "Then restart the server."
            ),
        )


def enforce_request_caps(
    content_type: str | None, content_length: int | None, max_mb: int
) -> None:
    """Reject a wrong request type or an oversized body BEFORE any read."""
    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        raise UnsupportedMediaTypeError("The upload must be a multipart form request.")
    if content_length is not None and content_length > max_mb * _BYTES_PER_MB:
        raise PayloadTooLargeError(
            f"The request body is over the {max_mb} MB upload cap."
        )


async def ingest_upload(
    source: UploadSource,
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    source_type: str | None = None,
    max_upload_mb: int | None = None,
) -> dict[str, Any]:
    """Spool one upload past the cap, then run the standard file ingest.

    The temp file keeps the client's extension, so ``ingest_file`` still
    auto-detects the document type. The temp file is always removed; the
    caller sees the ``ingest_file`` envelope unchanged. A body over the
    cap aborts with the typed 413 before any database write.
    """
    cap_mb = max_upload_mb if max_upload_mb is not None else upload_cap_mb(config)
    cap_bytes = cap_mb * _BYTES_PER_MB
    name = sanitize_filename(source.filename)
    if source.size is not None and source.size > cap_bytes:
        raise PayloadTooLargeError(f"{name} is over the {cap_mb} MB upload cap.")
    fd, temp_name = tempfile.mkstemp(prefix="corpus-upload-", suffix=Path(name).suffix)
    temp_path = Path(temp_name)
    try:
        received = 0
        with os.fdopen(fd, "wb") as sink:
            while True:
                chunk = await source.read(CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > cap_bytes:
                    raise PayloadTooLargeError(
                        f"{name} is over the {cap_mb} MB upload cap."
                    )
                sink.write(chunk)
        return await ingest_file(
            str(temp_path),
            pg_pool,
            config,
            tenant_id=tenant_id,
            source_type=source_type,
        )
    finally:
        temp_path.unlink(missing_ok=True)

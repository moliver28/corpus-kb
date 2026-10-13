"""Shared codecs and constants for the REFI-QDA Project bridge (U27).

The REFI-QDA Project standard (XSD ``urn:QDA-XML:project:1.0``) has no
notation for a codebook's MacQueen working fields or for a release manifest:
a ``Code`` element carries exactly ``guid``/``name``/``isCodable``/``color``
plus one free-text ``Description`` and nested child ``Code`` elements. The
codecs here pack everything the standard cannot express into that one
``Description`` string, behind an explicit marker, so that (a) other QDA
tools importing our files still see a readable plain-text description, and
(b) our import restores the original codebook byte-for-byte at the JSON
level.

Encoding contract (stable across corpus-kb versions):

    {brief_definition echo}

    [corpus-kb:meta]
    {canonical JSON of the full code dict}

The echo line is presentation only; on import the JSON block is the
authoritative payload. A ``Description`` without the marker is treated as a
foreign tool's plain description and imported with defaulted MacQueen fields
(never silently dropped).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

REFI_QDA_NAMESPACE = "urn:QDA-XML:project:1.0"

#: The REFI-QDA XSD GUIDType pattern (plain or brace-wrapped hex UUIDs).
GUID_PATTERN = re.compile(
    r"^(\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?)$"
)

META_MARKER = "[corpus-kb:meta]"

#: Deterministic UUIDv5 namespace for minted ``guid`` attributes. REFI-QDA
#: requires every Code/Set guid to match the XSD GUIDType pattern; codebook
#: code_ids are arbitrary strings, so guids are derived (not random) to keep
#: exports byte-stable across runs: uuid5(namespace, f"{codebook_id}:{id}").
GUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://corpus-kb.dev/refi-qda/project/1")


class RefiQdaError(ValueError):
    """Raised when a REFI-QDA Project document cannot be exported or imported."""


def mint_guid(scope: str, key: str) -> str:
    """Deterministic XSD-valid GUID for one named element within ``scope``."""
    return str(uuid.uuid5(GUID_NAMESPACE, f"{scope}:{key}"))


def canonical_json(value: object) -> str:
    """Deterministic JSON text (sorted keys, compact, literal UTF-8)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pack_description(plain_echo: str, meta: object) -> str:
    """The Description payload: human-readable echo plus the meta JSON block."""
    return f"{plain_echo}\n{META_MARKER}\n{canonical_json(meta)}"


def unpack_description(description: str) -> tuple[str, dict[str, Any] | None]:
    """Split a Description into (echo, meta).

    ``meta`` is None when the marker is absent (a foreign tool's plain
    description). A trailing block that fails to parse as a JSON object is
    ALSO reported as (whole text, None) — a malformed marker must degrade to
    plain text, never crash an import of a valid standards document.
    """
    echo, marker, tail = description.partition(META_MARKER)
    if not marker:
        return description, None
    try:
        meta = json.loads(tail)
    except ValueError:
        return description, None
    if not isinstance(meta, dict):
        return description, None
    return echo, meta


def normalize_code_id(code: dict[str, Any]) -> str:
    """The code's identifier under either accepted key (loader convention)."""
    for key in ("code_id", "id"):
        value = code.get(key)
        if isinstance(value, str) and value:
            return value
    raise RefiQdaError(f"code {code.get('name')!r} has no code_id/id")


def valid_guid(text: object) -> bool:
    return isinstance(text, str) and GUID_PATTERN.match(text) is not None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

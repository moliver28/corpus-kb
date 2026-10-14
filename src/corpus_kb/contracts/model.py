"""Operation contract types: the shared model behind parity and inventory.

One row (``Operation``) describes EVERY surface of one operation: its
tool/MCP name, HTTP route, socket method, CLI path, the parameters with
their defaults, and the success envelope keys. Two consumers read the SAME
rows, so they can never disagree:

  * ``tests/test_surface_parity_deep.py`` — runs the legs and fails on
    envelope drift.
  * ``corpus-kb operations`` (``cli_operations.py``) — emits the
    machine-readable inventory; ``--openapi`` adds the HTTP spec.

Keep this module import-light: standard library only, plus ``corpus_kb.errors``
(the single error table, merged at Wave 2 integration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corpus_kb.errors import ERROR_MAP as _ERRORS_ERROR_MAP

# The canonical placeholder tenant (RLS default; one definition per the
# program's hidden-knowledge rule — surfaces pass it as their default).
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Surface kinds (one op, many surfaces).
CLI = "cli"
MCP = "mcp"
HTTP = "http"
SOCKET = "socket"

# The error taxonomy has one owner: ``corpus_kb.errors`` (Wave 2a). This
# module re-exports the table under its contract name for the inventory.
ERROR_MAP: dict[str, tuple[int, int, int]] = dict(_ERRORS_ERROR_MAP)


def error_triplet(error_type: str) -> tuple[int, int, int]:
    """(http_status, socket_code, cli_exit) for one error type.

    Unknown types fall back to the internal triplet, never a silent zero.
    """
    return ERROR_MAP.get(error_type, ERROR_MAP["internal"])


@dataclass(frozen=True)
class ParamSpec:
    """One operation parameter: name, whether it is required, its default."""

    name: str
    required: bool = False
    default: Any = None


@dataclass(frozen=True)
class SurfaceSpec:
    """One surface of an operation.

    ``route`` is the HTTP path, the socket method name, the MCP tool name,
    or the CLI command path (without the ``corpus-kb`` prefix). ``method``
    is the HTTP verb only (``None`` elsewhere).
    """

    kind: str
    route: str
    method: str | None = None


@dataclass(frozen=True)
class Operation:
    """One cross-surface operation and every surface it is served on.

    ``envelope_keys`` lists the keys guaranteed present in the SUCCESS
    envelope on every surface (op-specific partial-state keys are extra).
    ``http_extra``/``socket_extra`` declare the additive keys a surface may
    carry over the shared envelope (undeclared extras are drift; the parity
    gate fails on them).
    """

    name: str
    family: str
    description: str
    handler: str
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    envelope_keys: tuple[str, ...] = field(default_factory=tuple)
    surfaces: tuple[SurfaceSpec, ...] = field(default_factory=tuple)
    http_extra: frozenset[str] = frozenset()
    socket_extra: frozenset[str] = frozenset()
    exit_codes: tuple[int, ...] | None = None


@dataclass(frozen=True)
class SkippedOperation:
    """An operation with two or more surfaces that the parity gate skips.

    Every skip carries an honest reason (live LLM/DB need, single-surface
    design, or a known pipeline split tracked elsewhere). ``exit_codes``
    optionally carries the documented CLI exit contract so the inventory
    stays complete for harness authors; ``None`` means the operation has
    no published exit-code table.
    """

    name: str
    family: str
    reason: str
    surfaces: tuple[SurfaceSpec, ...] = field(default_factory=tuple)
    exit_codes: tuple[int, ...] | None = None


def surfaces_of(op: Operation, kind: str) -> tuple[SurfaceSpec, ...]:
    """All surfaces of one kind (``http``, ``socket``, ``mcp``, ``cli``)."""
    return tuple(s for s in op.surfaces if s.kind == kind)

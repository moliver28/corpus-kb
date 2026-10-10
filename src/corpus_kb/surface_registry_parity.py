"""Surface-parity assertion for the research/coding surface registry (MECE).

``surface_registry.SURFACES`` is the ONE source of truth for the
research/coding command surfaces. This helper states the END-STATE contract:
every operation must be reachable on every declared transport (CLI / MCP /
HTTP). It is intentionally NOT yet enforced against the live registry — the
research surfaces are CLI+MCP only today (``http_route=None``); enforcement
lands in P5 together with the HTTP routes. Until then the consistency test
(``tests/test_surface_registry_consistency.py``) checks internal consistency
only, and this module ships test-covered so P5 can flip enforcement on.

STRICTLY STDLIB-ONLY, mirroring ``surface_registry.py``: zero ``corpus_kb``
or third-party imports, so path-based loaders can use both modules together.
"""

from __future__ import annotations

from collections.abc import Sequence


class SurfaceParityError(AssertionError):
    """One or more surfaces lack coverage on a declared transport."""


def _surface_field(surface: object, name: str) -> object:
    return getattr(surface, name, None)


def surface_coverage_gaps(registry: Sequence[object]) -> list[str]:
    """Describe every registry entry missing at least one transport.

    Returns one human-readable string per incomplete surface (empty list =
    full parity). A surface "counts" on a transport when the corresponding
    field is set: ``cli_path`` (CLI), ``mcp_tool`` (MCP), ``http_route``
    (HTTP).
    """
    gaps: list[str] = []
    for surface in registry:
        name = str(_surface_field(surface, "name") or "<unnamed>")
        missing: list[str] = []
        if not _surface_field(surface, "cli_path"):
            missing.append("cli")
        if not _surface_field(surface, "mcp_tool"):
            missing.append("mcp")
        if not _surface_field(surface, "http_route"):
            missing.append("http")
        if missing:
            gaps.append(f"{name}: missing {', '.join(missing)}")
    return gaps


def assert_full_surface_parity(registry: Sequence[object]) -> None:
    """Raise :class:`SurfaceParityError` if any surface lacks a transport.

    The error lists EVERY incomplete operation (MECE: no partial failures
    hide behind the first).
    """
    gaps = surface_coverage_gaps(registry)
    if gaps:
        listing = "\n".join(f"  - {gap}" for gap in gaps)
        raise SurfaceParityError(
            "surface registry is not transport-parity complete "
            f"({len(gaps)} of {len(registry)} operations incomplete):\n{listing}"
        )

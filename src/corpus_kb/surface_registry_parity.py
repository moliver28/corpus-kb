"""Surface-parity assertion for the research/coding surface registry (MECE).

``surface_registry.SURFACES`` is the ONE source of truth for the
research/coding command surfaces. This helper states the END-STATE contract:
every operation must be reachable on every declared transport (CLI / MCP /
HTTP). Since P7 (U5) the contract is ENFORCED against the live registry:
every research surface carries an HTTP route and the release lifecycle
ships on all three transports. The ONLY exceptions are the explicit,
documented ``TRANSPORT_ALLOWLIST`` below — honest staging, never silent
gaps (reconciliation note: ``docs/research/upgrade-reconciliation.md``).

STRICTLY STDLIB-ONLY, mirroring ``surface_registry.py``: zero ``corpus_kb``
or third-party imports, so path-based loaders can use both modules together.
"""

from __future__ import annotations

from collections.abc import Sequence

# Documented exceptions to full transport parity (surface name -> transports
# it is ALLOWED to miss). Everything else must be cli+mcp+http complete.
#
# * research-cycle: an interactive posture loop (in/on/out-of-loop approval
#   boundary, foreground --watch tailing) — not a mechanical
#   request/response, so it has no HTTP route by design.
# * research-demo: CLI-only by design (registry r13: a narrated demo needs
#   no MCP tool and no HTTP route; both render as absent on purpose).
TRANSPORT_ALLOWLIST: dict[str, frozenset[str]] = {
    "research-cycle": frozenset({"http"}),
    "research-demo": frozenset({"mcp", "http"}),
}


class SurfaceParityError(AssertionError):
    """One or more surfaces lack coverage on a declared transport."""


def _surface_field(surface: object, name: str) -> object:
    return getattr(surface, name, None)


def surface_coverage_gaps(
    registry: Sequence[object],
    allowlist: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """Describe every registry entry missing a transport it is not allowed to miss.

    Returns one human-readable string per incomplete surface (empty list =
    full parity outside the allowlist). A surface "counts" on a transport
    when the corresponding field is set: ``cli_path`` (CLI), ``mcp_tool``
    (MCP), ``http_route`` (HTTP). With ``allowlist`` (``TRANSPORT_ALLOWLIST``
    shape), a missing transport listed for that surface is an accepted
    exception, not a gap; a missing transport NOT in its entry still is.
    """
    gaps: list[str] = []
    for surface in registry:
        name = str(_surface_field(surface, "name") or "<unnamed>")
        allowed = (allowlist or {}).get(name) or frozenset()
        missing: list[str] = []
        for transport, field_name in (
            ("cli", "cli_path"),
            ("mcp", "mcp_tool"),
            ("http", "http_route"),
        ):
            if not _surface_field(surface, field_name) and transport not in allowed:
                missing.append(transport)
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


def assert_parity_outside_allowlist(registry: Sequence[object]) -> None:
    """Enforce parity with the documented :data:`TRANSPORT_ALLOWLIST`.

    Raises when a surface misses a transport its allowlist entry does not
    cover, AND for every allowlist entry that no longer matches reality (a
    surface that gained its missing transports must be REMOVED from the
    allowlist, not left as dead documentation).
    """
    undocumented = surface_coverage_gaps(registry, TRANSPORT_ALLOWLIST)
    incomplete = {gap.split(":")[0] for gap in surface_coverage_gaps(registry)}
    stale_entries = sorted(set(TRANSPORT_ALLOWLIST) - incomplete)
    if undocumented or stale_entries:
        lines = [f"  - {gap}" for gap in undocumented]
        lines += [
            f"  - allowlist entry '{name}' is stale: the surface now has full "
            "parity - remove the entry"
            for name in stale_entries
        ]
        raise SurfaceParityError(
            "surface registry is not transport-parity complete "
            f"({len(undocumented)} undocumented gaps, {len(stale_entries)} stale "
            f"allowlist entries):\n" + "\n".join(lines)
        )

"""Surface-parity assertion for the research/coding surface registry (MECE).

``surface_registry.SURFACES`` is the ONE source of truth for the
research/coding command surfaces. This helper states the END-STATE contract:
every operation must be complete on every transport it DECLARES (CLI / MCP /
HTTP). Each ``Surface`` row carries a per-op surface set (``surfaces``);
the default is the full trio, so every pre-existing row stays cli+mcp+http
complete and :func:`assert_full_surface_parity` holds against the live
registry. The one declared exception today is the multipart upload row
(``ingest-upload``, HTTP-only by nature — ruling 4a).

The :data:`TRANSPORT_ALLOWLIST` mechanism stays (an empty dict) as the
documented-exception fallback: if a surface genuinely cannot serve a
transport it declares, add it here with an honest reason and switch the
enforcement to :func:`assert_parity_outside_allowlist`.

STRICTLY STDLIB-ONLY, mirroring ``surface_registry.py``: zero ``corpus_kb``
or third-party imports, so path-based loaders can use both modules together.
"""

from __future__ import annotations

from collections.abc import Sequence

# The transports a row declares by default (full parity), and the Surface
# field each transport must set.
DEFAULT_TRANSPORTS: tuple[str, ...] = ("cli", "mcp", "http")
_TRANSPORT_FIELDS: dict[str, str] = {
    "cli": "cli_path",
    "mcp": "mcp_tool",
    "http": "http_route",
}

# Documented exceptions to declared transport parity (surface name ->
# transports it is ALLOWED to miss). EMPTY: the per-op ``surfaces`` set is
# the primary declaration; this fallback stays for a future honest gap.
TRANSPORT_ALLOWLIST: dict[str, frozenset[str]] = {}


class SurfaceParityError(AssertionError):
    """One or more surfaces lack coverage on a declared transport."""


def _surface_field(surface: object, name: str) -> object:
    return getattr(surface, name, None)


def _declared_transports(surface: object) -> tuple[str, ...]:
    """The surface's declared transport set (the full trio when unset)."""
    declared = getattr(surface, "surfaces", None)
    if not declared:
        return DEFAULT_TRANSPORTS
    return tuple(declared)


def surface_coverage_gaps(
    registry: Sequence[object],
    allowlist: dict[str, frozenset[str]] | None = None,
) -> list[str]:
    """Describe every registry entry incomplete on its declared transports.

    Returns one human-readable string per incomplete surface (empty list =
    every surface complete within its declared set). A surface "counts" on
    a transport when the corresponding field is set: ``cli_path`` (CLI),
    ``mcp_tool`` (MCP), ``http_route`` (HTTP). Three findings are gaps:
    a declared transport with an unset field, an undeclared transport with
    a set field (the row over-declares), and an unknown declared name.
    With ``allowlist`` (``TRANSPORT_ALLOWLIST`` shape), a missing transport
    listed for that surface is an accepted exception, not a gap.
    """
    gaps: list[str] = []
    for surface in registry:
        name = str(_surface_field(surface, "name") or "<unnamed>")
        allowed = (allowlist or {}).get(name) or frozenset()
        declared = _declared_transports(surface)
        parts: list[str] = []
        missing = [
            transport
            for transport in declared
            if transport not in _TRANSPORT_FIELDS
            or (
                not _surface_field(surface, _TRANSPORT_FIELDS[transport])
                and transport not in allowed
            )
        ]
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        undeclared = [
            f"{transport} set but not declared"
            for transport in DEFAULT_TRANSPORTS
            if transport not in declared and _surface_field(surface, _TRANSPORT_FIELDS[transport])
        ]
        parts.extend(undeclared)
        if parts:
            gaps.append(f"{name}: {', '.join(parts)}")
    return gaps


def assert_full_surface_parity(registry: Sequence[object]) -> None:
    """Raise :class:`SurfaceParityError` if any surface misses a declared transport.

    "Full parity" means complete within each surface's declared transport
    set. The error lists EVERY incomplete operation (MECE: no partial
    failures hide behind the first).
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

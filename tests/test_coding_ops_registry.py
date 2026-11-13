"""The surface registry as live code (issue #350 rows).

The registry is normally consumed by source inspection and the
generators. One test imports it, so the module body executes under
coverage and the issue-350 entries are pinned as real objects: their
names, CLI paths, HTTP routes, and the wrappers that cover them.
"""

from __future__ import annotations

from corpus_kb.surface_registry import ACTIVE_WRAPPERS, SURFACES, WRAPPERS

_ISSUE_350_SURFACES = {
    "coding-embed-codebook",
    "coding-pool",
    "coding-code-batch",
    "coding-reliability",
    "coding-calibrate-floors",
    "coding-saturation",
    "coding-status",
    "coding-export",
    "events-summary",
}


def test_issue_350_rows_are_registered_and_coherent() -> None:
    """The coding/event surfaces exist once, with routes and wrappers."""
    names = [s.name for s in SURFACES]
    for name in _ISSUE_350_SURFACES:
        assert names.count(name) == 1, f"{name} must register exactly once"

    by_name = {s.name: s for s in SURFACES}
    assert by_name["coding-status"].http_route == "/api/coding/status"
    assert by_name["coding-status"].cli_path == "coding status"
    assert by_name["events-summary"].http_route == "/api/events/summary"
    assert by_name["events-summary"].cli_path == "events"

    covers = {c for w in WRAPPERS for c in w.covers}
    assert covers >= _ISSUE_350_SURFACES
    active = {w.name for w in ACTIVE_WRAPPERS}
    assert active >= {"corpus-coding-ops", "corpus-events"}
    wrapper = {w.name: w for w in WRAPPERS}
    assert wrapper["corpus-coding-ops"].argument_hint
    assert wrapper["corpus-events"].argument_hint == "[--json]"

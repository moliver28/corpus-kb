"""UI op manifest gates (UI SPA program Task 4).

The manifest is the ONE op table the SPA talks through (zero-drift rule 1).
Three pins:

(a) Fresh generation reproduces every committed artifact byte for byte —
    the JSON manifest AND both generated TypeScript modules (``poe gen``
    runs the same comparison in ``--check`` mode);
(b) Every manifest path exists in ``create_http_app()`` with the manifest's
    method — a route rename or method change anywhere fails here;
(c) The spec's longRunning flags and the merged tool-route bodies match.
"""

from __future__ import annotations

import json
from pathlib import Path

from corpus_kb._setup.gen_ui_manifest import (
    artifacts,
    build_manifest,
)
from corpus_kb.config import VERIFY_FULL_GROUNDEDNESS

REPO = Path(__file__).resolve().parent.parent

EXPECTED_ARTIFACTS = (
    "src/corpus_kb/ui_manifest.json",
    "ui_src/src/api/ops.generated.ts",
    "ui_src/src/api/thresholds.generated.ts",
)


def test_fresh_generation_matches_committed_artifacts() -> None:
    """Regenerating in memory reproduces all three committed files exactly."""
    generated = artifacts(REPO)
    assert set(generated) == {Path(rel) for rel in EXPECTED_ARTIFACTS}
    for rel, text in generated.items():
        committed = (REPO / rel).read_text(encoding="utf-8")
        assert committed == text, f"{rel.as_posix()} is stale — run poe gen"


def test_ui_src_manifest_copy_matches_the_generated_source() -> None:
    """The SPA's importable copy byte-matches the generated manifest.

    The pnpm pre-hooks copy the file, but they are not a guarantee: a
    stale copy must fail here. On drift run ``poe gen`` (or
    ``pnpm -C ui_src run copy-manifest``).
    """
    source = (REPO / "src/corpus_kb/ui_manifest.json").read_bytes()
    copy = (REPO / "ui_src/src/api/manifest.json").read_bytes()
    assert copy == source, (
        "ui_src/src/api/manifest.json is stale — run: poe gen "
        "(or: pnpm -C ui_src run copy-manifest)"
    )


def test_manifest_json_is_sorted_and_complete() -> None:
    """The committed JSON parses, sorts by path then method, and covers every
    registry surface that declares an http_route."""
    from corpus_kb import surface_registry as reg

    manifest = json.loads((REPO / "src/corpus_kb/ui_manifest.json").read_text(encoding="utf-8"))
    expected = {s.http_route for s in reg.SURFACES if s.http_route}
    assert {str(e["path"]) for e in manifest} == expected
    keys = [(str(e["path"]), str(e["method"])) for e in manifest]
    assert keys == sorted(keys)


def test_every_manifest_path_exists_with_the_real_method() -> None:
    """Route table cross-check: path present, method set equal (minus HEAD)."""
    from corpus_kb.api.http import create_http_app

    route_table = {route.path: route.methods - {"HEAD"} for route in create_http_app().routes}
    for entry in build_manifest(REPO):
        method = str(entry["method"])
        assert route_table[str(entry["path"])] == {method}, str(entry["id"])


def test_manifest_entry_shape() -> None:
    """Every entry carries exactly the spec D.2 keys with typed values."""
    allowed = {
        "id",
        "path",
        "method",
        "description",
        "required",
        "optional",
        "paramsKnown",
        "longRunning",
    }
    for entry in build_manifest(REPO):
        assert set(entry) == allowed, str(entry["id"])
        assert isinstance(entry["required"], list) and isinstance(entry["optional"], list)
        assert entry["paramsKnown"] is False
        assert isinstance(entry["longRunning"], bool)


def test_long_running_flags_match_the_spec() -> None:
    """Exactly these D.2 op ids are flagged longRunning.

    The expectation is pinned as string literals (not the generator's
    constant) so a silent generator change fails loudly. Task 5a adds the
    ingest/upload and doctor rows and MUST revisit these flags then.
    """
    expected = frozenset(
        {
            "research-cycle",
            "research-demo",
            "coding-run",
            "coding-inductive",
            "topics-build",
            "analyze-run",
            "labeling-build",
            "classification-build",
            "research-ingest",
        }
    )
    flagged = {str(e["id"]) for e in build_manifest(REPO) if e["longRunning"]}
    assert flagged == expected


def test_tool_route_bodies_are_merged() -> None:
    """required/optional in the manifest mirror research_http.tool_routes()."""
    from corpus_kb.api.research_http import tool_routes

    manifest = {str(e["path"]): e for e in build_manifest(REPO)}
    for route in tool_routes():
        entry = manifest[route.path]
        assert entry["required"] == list(route.required), route.path
        assert entry["optional"] == list(route.optional), route.path


def test_generated_ops_table_lists_every_manifest_entry() -> None:
    """The TypeScript op table has one row per manifest id, same method."""
    entries = build_manifest(REPO)
    ops_ts = (REPO / "ui_src/src/api/ops.generated.ts").read_text(encoding="utf-8")
    for entry in entries:
        assert f'"{entry["id"]}": {{ path: "{entry["path"]}", method: "{entry["method"]}"' in (
            ops_ts
        ), str(entry["id"])


def test_thresholds_artifact_tracks_the_config_default() -> None:
    """The badge threshold keeps ONE policy source: corpus_kb.config."""
    thresholds_ts = (REPO / "ui_src/src/api/thresholds.generated.ts").read_text("utf-8")
    assert f"verifyFullGroundedness: {VERIFY_FULL_GROUNDEDNESS}," in thresholds_ts

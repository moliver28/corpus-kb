"""Generate the UI op manifest and the typed client artifacts (UI SPA Task 4).

Outputs (all COMMITTED — ``--check`` regenerates in memory and exits 1 on
drift, ``gen_harness_pack`` precedent):

  * ``src/corpus_kb/ui_manifest.json``         — the op manifest (sorted JSON)
  * ``ui_src/src/api/ops.generated.ts``        — typed op table: id, path,
    method, longRunning (an ``@generated`` file humans must not edit)
  * ``ui_src/src/api/thresholds.generated.ts`` — verify-badge thresholds
    codegen'd from ``corpus_kb.config`` policy defaults (compile-time; the
    values never travel over the wire)

Sources (exactly two, zero-drift rule 1):

  * ``surface_registry.SURFACES``, loaded BY PATH — the registry is
    strictly stdlib-only and is never imported as a package member;
  * ``corpus_kb.api.research_http.tool_routes()`` for the required/optional
    body keys (imported lazily; the registry load stays dependency-free).

The generator imports ``VERIFY_FULL_GROUNDEDNESS`` from ``corpus_kb.config``
so the badge threshold keeps ONE policy source; the value is emitted into
the generated TypeScript module, and nowhere else in ``ui_src/``.

Usage:
    python src/corpus_kb/_setup/gen_ui_manifest.py [--check] [--project-root PATH]

Exit code 0 = in sync (or written), 1 = drift detected.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from corpus_kb.config import VERIFY_FULL_GROUNDEDNESS

REGISTRY_REL = Path("src") / "corpus_kb" / "surface_registry.py"
MANIFEST_REL = Path("src") / "corpus_kb" / "ui_manifest.json"
OPS_TS_REL = Path("ui_src") / "src" / "api" / "ops.generated.ts"
THRESHOLDS_TS_REL = Path("ui_src") / "src" / "api" / "thresholds.generated.ts"

GENERATOR_NAME = "src/corpus_kb/_setup/gen_ui_manifest.py"

# D.2 method rule: list routes answer GET; every other registry route is a
# synchronous POST. tests/test_ui_manifest.py pins these against the REAL
# method sets in create_http_app(), so a route method change fails here.
GET_SURFACE_NAMES = frozenset(
    {
        "release-status",
        "ingest-status",
        "doctor",
        "topics-list",
        "labeling-list",
        "labeling-lfs",
        "classification-list",
    }
)

# D.2 longRunning list: ops whose receipt can take minutes. UI SPA Task 5a
# added the ingest-directory and ingest-url registry rows, so the full spec
# list is flaggable (tests/test_ui_manifest.py pins the same ids).
LONG_RUNNING_OP_IDS = frozenset(
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
        "ingest-directory",
        "ingest-url",
        # UI SPA Task 5b (issue #176): the sample-corpus load embeds every
        # file on a cold host, so the SPA shows the elapsed-time state.
        "demo-load",
    }
)


def load_registry(project_root: Path) -> ModuleType:
    """Load surface_registry.py by path — never imports the package tree."""
    path = project_root / REGISTRY_REL
    if not path.exists():
        raise SystemExit(f"surface registry not found: {path}")
    spec = importlib.util.spec_from_file_location("corpus_surface_registry", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load surface registry: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_route_bodies() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """path -> (required, optional) from the declarative research routes."""
    from corpus_kb.api.research_http import tool_routes

    return {route.path: (route.required, route.optional) for route in tool_routes()}


def build_manifest(project_root: Path) -> list[dict[str, object]]:
    """One manifest entry per registry surface that declares an http_route."""
    reg = load_registry(project_root)
    bodies = _tool_route_bodies()
    entries: list[dict[str, object]] = []
    for surface in reg.SURFACES:
        if not surface.http_route:
            continue
        required, optional = bodies.get(surface.http_route, ((), ()))
        entries.append(
            {
                "id": surface.name,
                "path": surface.http_route,
                "method": "GET" if surface.name in GET_SURFACE_NAMES else "POST",
                "description": surface.description,
                "required": list(required),
                "optional": list(optional),
                "paramsKnown": False,
                "longRunning": surface.name in LONG_RUNNING_OP_IDS,
            }
        )
    entries.sort(key=lambda entry: (str(entry["path"]), str(entry["method"])))
    return entries


def _ts_string(value: str) -> str:
    """A double-quoted TypeScript string literal (JSON escaping is valid TS)."""
    return json.dumps(value, ensure_ascii=False)


def render_ops_ts(entries: list[dict[str, object]]) -> str:
    """The typed op table the SPA client resolves ops through."""
    lines = [
        f"// @generated by {GENERATOR_NAME} — DO NOT EDIT.",
        "// Sources: surface_registry.SURFACES + api.research_http.tool_routes().",
        "// Regenerate with: poe gen (the drift check reverts hand edits).",
        "",
        "export interface OpDescriptor {",
        "  path: string;",
        '  method: "GET" | "POST";',
        "  longRunning: boolean;",
        "}",
        "",
        "export const OPS: Readonly<Record<string, OpDescriptor>> = {",
    ]
    for entry in entries:
        lines.append(
            f"  {_ts_string(str(entry['id']))}: {{ path: {_ts_string(str(entry['path']))}, "
            f"method: {_ts_string(str(entry['method']))}, "
            f"longRunning: {'true' if entry['longRunning'] else 'false'} }},"
        )
    lines.extend(
        [
            "};",
            "",
            "/** One op table row, or undefined when the id is not in the manifest. */",
            "export function getOp(opId: string): OpDescriptor | undefined {",
            "  return OPS[opId];",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def render_thresholds_ts() -> str:
    """The verify-badge thresholds, codegen'd from the config policy default."""
    return (
        f"// @generated by {GENERATOR_NAME} from corpus_kb.config — DO NOT EDIT.\n"
        "// Policy source: VERIFY_FULL_GROUNDEDNESS (src/corpus_kb/config.py).\n"
        "// No numeric policy literal may appear anywhere else in ui_src/.\n"
        '// A missing or invalid threshold degrades the badge to "not verified".\n'
        "\n"
        "export interface BadgeThresholds {\n"
        "  verifyFullGroundedness: number;\n"
        "}\n"
        "\n"
        "export const BADGE_THRESHOLDS: BadgeThresholds = {\n"
        f"  verifyFullGroundedness: {VERIFY_FULL_GROUNDEDNESS},\n"
        "};\n"
    )


def artifacts(project_root: Path) -> dict[Path, str]:
    """Every generated artifact as a repo-relative path -> text (LF endings)."""
    entries = build_manifest(project_root)
    manifest_json = json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return {
        MANIFEST_REL: manifest_json,
        OPS_TS_REL: render_ops_ts(entries),
        THRESHOLDS_TS_REL: render_thresholds_ts(),
    }


def run(project_root: Path, check: bool) -> int:
    """Write the artifacts, or (with --check) exit 1 on any drift."""
    generated = artifacts(project_root)
    if not check:
        for rel, text in generated.items():
            path = project_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {rel.as_posix()}")
        return 0
    drift = []
    for rel, text in generated.items():
        path = project_root / rel
        try:
            committed = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(
                f"drift: {rel.as_posix()} is missing — run the generator without --check",
                file=sys.stderr,
            )
            drift.append(rel.as_posix())
            continue
        if committed != text:
            print(
                f"drift: {rel.as_posix()} is stale — run the generator without --check",
                file=sys.stderr,
            )
            drift.append(rel.as_posix())
    if drift:
        return 1
    print("ui manifest artifacts in sync")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (scripts/gen_ui_manifest.py delegates here)."""
    doc = __doc__ or "Generate the UI op manifest artifacts."
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(args.project_root.resolve(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())

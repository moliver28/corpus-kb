"""Generate the UI op manifest and the typed client artifacts (UI SPA Task 4).

Four COMMITTED outputs (``--check`` regenerates in memory and exits 1 on
drift): ``ui_manifest.json`` (sorted op manifest), ``manifest.json``
(the bundled copy without ``description`` keys), ``ops.generated.ts``
(typed op table), and ``thresholds.generated.ts`` (verify thresholds).
The renderers live in ``gen_ui_manifest_render.py`` (the 250-line rule).

Sources (exactly two, zero-drift rule 1): ``surface_registry.SURFACES``
loaded BY PATH (stdlib-only, never a package member), and
``research_http.tool_routes()`` for the body keys (lazy import). One
declared exception: the multipart upload row names its ``file`` form
field in :data:`MULTIPART_BODY_KEYS` below.
>>>>>>> origin/master

Usage:
    python src/corpus_kb/_setup/gen_ui_manifest.py [--check] [--project-root PATH]

Exit code 0 = in sync (or written), 1 = drift. Methods: gen_method_register_docs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from corpus_kb._setup.gen_ui_manifest_methods import build_method_entries
from corpus_kb._setup.gen_ui_manifest_render import (
    METHODS_TS_REL,
    OPS_TS_REL,
    THRESHOLDS_TS_REL,
    render_methods_ts,
    render_ops_ts,
    render_thresholds_ts,
)

REGISTRY_REL = Path("src") / "corpus_kb" / "surface_registry.py"
MANIFEST_REL = Path("src") / "corpus_kb" / "ui_manifest.json"
BUNDLED_MANIFEST_REL = Path("ui_src") / "src" / "api" / "manifest.json"

# D.2 method rule: list routes answer GET, the document delete answers
# DELETE, and every other registry route is a synchronous POST.
# tests/test_ui_manifest.py pins these against the REAL method sets in
# create_http_app(), so a route method change fails here.
GET_SURFACE_NAMES = frozenset(
    {
        "release-status",
        "ingest-status",
        "doctor",
        "projects-list",
        "codebooks-list",
        "codebook-proposals",
        "coding-assignments",
        "topics-list",
        "labeling-list",
        "labeling-lfs",
        "classification-list",
        # Issue #182: the documents/tags/metadata reads the SPA resolves.
        "documents-list",
        "document-tags",
        "document-metadata",
        # Phase-2 Wave 5: the drawer NLP panel's chunk window read.
        "chunks-list",
        # Phase-2 Wave 3: the corpus stats pair (GET routes predate the rows).
        "stats",
        "document-stats",
        # Phase-2 Wave 4: the graph reads the workbench tables call (the
        # relations read answers GET by entity id; the entity list window).
        "get-entity-relations",
        "list-entities",
        # Phase-2 Wave 6: the Map's edge window read.
        "list-relations",
        # Issue #350: the coding status read and the event-store health read.
        "coding-status",
        "events-summary",
    }
)

DELETE_SURFACE_NAMES = frozenset({"document-delete"})

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
        # Issue #350: the chunk-coding ops that embed, pool, or code a
        # batch can run minutes on a real corpus.
        "coding-embed-codebook",
        "coding-pool",
        "coding-code-batch",
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


# The multipart upload route (registry row ingest-upload) is not a JSON
# tool route: its one required form field is the file itself. The route
# table above cannot express it, so the multipart shape lives here.
MULTIPART_BODY_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "/api/ingest/upload": (("file",), ()),
}


def build_manifest(project_root: Path) -> list[dict[str, object]]:
    """One manifest entry per registry surface that declares an http_route."""
    reg = load_registry(project_root)
    bodies = _tool_route_bodies()
    entries: list[dict[str, object]] = []
    for surface in reg.SURFACES:
        if not surface.http_route:
            continue
        required, optional = bodies.get(
            surface.http_route, MULTIPART_BODY_KEYS.get(surface.http_route, ((), ()))
        )
        entries.append(
            {
                "id": surface.name,
                "path": surface.http_route,
                "method": (
                    "DELETE"
                    if surface.name in DELETE_SURFACE_NAMES
                    else "GET"
                    if surface.name in GET_SURFACE_NAMES
                    else "POST"
                ),
                "description": surface.description,
                "required": list(required),
                "optional": list(optional),
                "paramsKnown": False,
                "longRunning": surface.name in LONG_RUNNING_OP_IDS,
            }
        )
    entries.sort(key=lambda entry: (str(entry["path"]), str(entry["method"])))
    return entries


def _bundled_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """The bundle copy: every entry minus its description.

    The SPA never renders the description (nothing in ui_src reads it),
    so the browser bundle does not pay for ~13 kB of JSON text. The repo
    manifest keeps the descriptions — the harness parity gates read them
    there. One writer emits both copies, so they cannot disagree on any
    other key (Wave-5 bundle-headroom lever).
    """
    bundled: list[dict[str, object]] = []
    for entry in entries:
        row = {key: value for key, value in entry.items() if key != "description"}
        bundled.append(row)
    return bundled


def artifacts(project_root: Path) -> dict[Path, str]:
    """Every generated artifact as a repo-relative path -> text (LF endings)."""
    entries = build_manifest(project_root)
    manifest_json = json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    bundled_json = (
        json.dumps(_bundled_entries(entries), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return {
        MANIFEST_REL: manifest_json,
        BUNDLED_MANIFEST_REL: bundled_json,
        OPS_TS_REL: render_ops_ts(entries),
        THRESHOLDS_TS_REL: render_thresholds_ts(),
        METHODS_TS_REL: render_methods_ts(build_method_entries(project_root)),
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

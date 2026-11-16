#!/usr/bin/env python3
"""Generate the method table block in docs/ANALYSIS-METHODS.md (AMR program).

Reads the register YAML (``config/analysis_methods.yaml``, the repo truth)
and rewrites ONLY the block between the exact markers below; the static
header around it is hand-maintained:

  <!-- amr-register:generated:start -->
  ...the table plus the per-method guidance bullets...
  <!-- amr-register:generated:end -->

The block builder lives in ``_method_docs_block.py`` (the sibling-helper
pattern the bench scripts use), so both files stay under the 250-line cap.

``--check`` regenerates in memory and exits 1 on drift (the license-lock
and harness-pack precedent) so ``poe gen`` / ``poe check`` catch a stale
docs table after any register edit.

Usage:
    python scripts/gen_method_register_docs.py [--check] [--project-root PATH]

Exit code 0 = in sync (or written), 1 = drift detected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from corpus_kb.analysis.method_register_dataflow import ladder_next, ladder_prerequisites
from corpus_kb.analysis.method_register_difficulty import difficulty_mismatches
from corpus_kb.analysis.method_register_loader import load_method_register

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _method_docs_block import END_MARKER, START_MARKER, build_block

REGISTER_REL = Path("config") / "analysis_methods.yaml"
DOCS_REL = Path("docs") / "ANALYSIS-METHODS.md"


def load_register(project_root: Path) -> dict[str, Any]:
    """Parse the register YAML (the packaged mirror mirrors it byte-for-byte)."""
    raw: Any = yaml.safe_load((project_root / REGISTER_REL).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "methods" not in raw:
        raise SystemExit(f"register YAML has no methods block: {project_root / REGISTER_REL}")
    return raw


def ladder_views(
    project_root: Path,
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """The derived ladder maps plus the difficulty report (one validated load)."""
    register = load_method_register(
        {"analysis": {"method_register_path": str(project_root / REGISTER_REL)}}
    )
    methods = register.methods
    return (
        {row.id: ladder_prerequisites(methods, row.id) for row in methods},
        {row.id: ladder_next(methods, row.id) for row in methods},
        difficulty_mismatches(register),
    )


def regenerate(project_root: Path, check: bool) -> int:
    """Rewrite the generated block, or report drift under --check."""
    docs_path = project_root / DOCS_REL
    text = docs_path.read_text(encoding="utf-8")
    start = text.index(START_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    prereqs_by_id, next_by_id, report_lines = ladder_views(project_root)
    generated = build_block(load_register(project_root), prereqs_by_id, next_by_id, report_lines)
    updated = text[:start] + generated + text[end:]
    if check:
        if text == updated:
            return 0
        print(f"drift: {DOCS_REL} does not match {REGISTER_REL}", file=sys.stderr)
        return 1
    docs_path.write_text(updated, encoding="utf-8")
    print(f"wrote {docs_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    return regenerate(args.project_root, args.check)


if __name__ == "__main__":
    raise SystemExit(main())

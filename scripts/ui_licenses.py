#!/usr/bin/env python3
"""UI production-license gate (UI SPA program Task 4).

Runs ``pnpm licenses list`` over ``ui_src/`` and enforces the production
allowlist: MIT, Apache-2.0, BSD, ISC, 0BSD (plus CC0-1.0, MIT-0).
The lockfile records the production tree only: dev trees contain
platform-specific native packages (rolldown/esbuild bindings), so a
dev snapshot would differ per OS and never stay in sync.

Outputs (COMMITTED — ``--check`` regenerates in memory and exits 1 on
drift, ``gen_harness_pack`` precedent):

  * ``ui_licenses.lock.json`` — name -> license for the production
    dependency tree, so a license change shows up in review.

Usage:
    python scripts/ui_licenses.py [--check] [--project-root PATH]

Exit code 0 = licenses within the allowlist (and lock in sync with
``--check``), 1 = an offending production license or lock drift.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

LOCK_REL = Path("ui_licenses.lock.json")
UI_SRC_REL = Path("ui_src")

ALLOWED_EXACT = frozenset({"MIT", "Apache-2.0", "ISC", "0BSD", "CC0-1.0", "MIT-0"})
_ALLOWED_PREFIXES = ("BSD",)
_BRANCH_RX = re.compile(r"\s+(?:OR|AND)\s+")


def _license_allowed(text: str) -> bool:
    """True when every branch of the license expression is allowlisted."""
    normalized = text.strip().replace("(", "").replace(")", "").replace("*", "")
    branches = [branch.strip() for branch in _BRANCH_RX.split(normalized)]
    return all(
        branch in ALLOWED_EXACT or branch.startswith(_ALLOWED_PREFIXES)
        for branch in branches
        if branch
    )


def _pnpm_executable() -> str:
    """The pnpm on PATH (Windows shims are .cmd files — resolve, don't guess)."""
    found = shutil.which("pnpm")
    if found is None:
        raise SystemExit("pnpm was not found. Install pnpm 12 (never via corepack).")
    return found


def _pnpm_licenses(project_root: Path, prod_only: bool) -> dict[str, str]:
    """pnpm licenses list --json flattened to name -> license string."""
    cmd = [_pnpm_executable(), "-C", str(project_root / UI_SRC_REL), "licenses", "list", "--json"]
    if prod_only:
        cmd.append("--prod")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"pnpm licenses list failed: {result.stderr.strip()}")
    grouped: dict[str, list[dict[str, object]]] = json.loads(result.stdout)
    flat: dict[str, str] = {}
    for license_name, packages in grouped.items():
        for package in packages:
            name = str(package.get("name", ""))
            flat[name] = str(package.get("license", license_name))
    return dict(sorted(flat.items()))


def build_lock(project_root: Path) -> tuple[dict[str, str], str]:
    """(production, rendered-lock) — prod gated and recorded, sorted."""
    production = _pnpm_licenses(project_root, prod_only=True)
    lock = {
        "gate": "production allowlist: MIT, Apache-2.0, BSD, ISC, 0BSD (plus CC0-1.0, MIT-0)",
        "production": production,
    }
    rendered = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    return production, rendered


def run(project_root: Path, check: bool) -> int:
    """Write ui_licenses.lock.json, or (with --check) verify lock + gate."""
    production, rendered = build_lock(project_root)
    offenders = sorted(name for name, lic in production.items() if not _license_allowed(lic))
    if offenders:
        for name in offenders:
            print(
                f"production dependency {name}: {production[name]} — "
                "not on the production allowlist",
                file=sys.stderr,
            )
        return 1
    lock_path = project_root / LOCK_REL
    if check:
        if not lock_path.exists() or lock_path.read_text(encoding="utf-8") != rendered:
            print(
                f"drift: {LOCK_REL.as_posix()} is stale or missing — run the gate without --check",
                file=sys.stderr,
            )
            return 1
        print(f"ui licenses within the allowlist ({len(production)} production deps)")
        return 0
    lock_path.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {LOCK_REL.as_posix()} ({len(production)} production deps)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (the poe ui-licenses target)."""
    doc = __doc__ or "UI production-license gate."
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(args.project_root.resolve(), check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())

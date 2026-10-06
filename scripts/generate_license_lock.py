from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import deque
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any

from packaging.markers import Marker, Variable
from packaging.requirements import Requirement


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


EXCLUDED_PACKAGES = {
    _normalize(name)
    for name in {
        "corpus-kb",
        "python-magic-bin",
        "pywin32",
        # `colorama` is only pulled in on Windows (typer/tqdm/wasabi gate it
        # behind `platform_system == "Windows"` markers). Excluding it keeps
        # the lock identical whether it is generated on Windows or Linux.
        "colorama",
        # `tzdata` is only pulled in on Windows (psycopg gates it behind a
        # `platform_system == "Windows"` marker). Same cross-platform rule
        # as colorama: exclude so the lock is platform-independent.
        "tzdata",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "syrupy",
        "coverage",
        # `pip` and `wheel` ship with the interpreter and aren't runtime
        # dependencies of the project. `setuptools` is intentionally kept
        # here because `llama-index-core>=0.11` declares a runtime
        # `setuptools>=80.9.0` requirement.
        "pip",
        "wheel",
    }
}

# Canonical SPDX-like short names for the most common license identifiers
# encountered in distribution metadata. The key is matched case-insensitively
# after lowercasing and collapsing whitespace, so "MIT License", "mit",
# "MIT license" all map to the same canonical "MIT" form. This keeps the
# lock deterministic across wheels/installs that emit slightly different
# strings for the same license.
LICENSE_NORMALIZATION = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache-2.0 and mit": "Apache-2.0 AND MIT",
    "mit and psf-2.0": "MIT AND PSF-2.0",
    "apache-2.0 and cnri-python": "Apache-2.0 AND CNRI-Python",
    "apache-2.0 or bsd-2-clause": "Apache-2.0 OR BSD-2-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "isc license (iscl)": "ISC",
    "isc": "ISC",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl-2.0 and mit": "MPL-2.0 AND MIT",
    "mit-cmu": "MIT-CMU",
    "python software foundation license": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "academic free license (afl)": "AFL-3.0",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "gnu library or lesser general public license (lgpl)": "LGPL",
}

LICENSE_TEXT_URLS = {
    "MIT": "https://opensource.org/licenses/MIT",
    "Apache-2.0": "https://opensource.org/licenses/Apache-2.0",
    "BSD-2-Clause": "https://opensource.org/licenses/BSD-2-Clause",
    "BSD-3-Clause": "https://opensource.org/licenses/BSD-3-Clause",
    "ISC": "https://opensource.org/licenses/ISC",
    "MPL-2.0": "https://opensource.org/licenses/MPL-2.0",
    "LGPL": "https://opensource.org/licenses/LGPL-3.0",
    "PSF-2.0": "https://docs.python.org/3/license.html#psf-license",
    "AFL-3.0": "https://opensource.org/licenses/AFL-3.0",
    "HPND": "https://opensource.org/licenses/HPND",
    "MIT-CMU": "https://github.com/python-pillow/Pillow/blob/main/LICENSE",
}

# Some distributions ship platform-specific wheels whose license metadata
# differs: llvmlite's Linux wheel declares a PEP 639 License-Expression
# ("BSD-2-Clause AND Apache-2.0 WITH LLVM-exception") while its Windows wheel
# only ships License-File entries, so the generic extractor reports UNKNOWN.
# Pin those to a canonical value so the lock is identical on every platform.
LICENSE_OVERRIDES = {
    "llvmlite": "BSD-2-Clause AND Apache-2.0 WITH LLVM-exception",
}


def _installed_distributions() -> dict[str, Distribution]:
    return {_normalize(dist.metadata["Name"]): dist for dist in distributions()}


def _read_project_dependencies() -> list[Requirement]:
    """Parse the ``[project] dependencies`` table from ``pyproject.toml``."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return [Requirement(dep) for dep in pyproject["project"]["dependencies"]]


def _marker_uses_extra(marker: Marker) -> bool:
    """Return True if the marker references the ``extra`` variable."""

    def _walk(node: object) -> bool:
        if isinstance(node, list):
            return any(_walk(item) for item in node)
        if isinstance(node, tuple):
            return any(_walk(item) for item in node)
        if isinstance(node, Variable):
            return node.value == "extra"
        return False

    return _walk(marker._markers)


def _requirement_active(req: Requirement, active_extras: frozenset[str]) -> bool:
    """Decide whether a requirement applies given the parent's active extras.

    ``extra`` markers (e.g. ``extra == "crypto"``) are evaluated against each
    extra active on the parent, so ``mcp[cli]`` pulls in the ``cli``-gated
    requirements. Markers that do not reference ``extra`` are evaluated
    against the default environment.
    """
    if req.marker is None:
        return True
    if _marker_uses_extra(req.marker):
        return any(req.marker.evaluate({"extra": extra}) for extra in active_extras)
    return req.marker.evaluate()


def _resolve_packages(
    installed: dict[str, Distribution],
) -> dict[str, Distribution]:
    """Resolve the runtime dependency closure via extra-aware BFS.

    The walk starts from the ``[project] dependencies`` declared in
    ``pyproject.toml`` and follows each installed distribution's
    ``Requires-Dist`` metadata. A requirement is included only when its
    marker is satisfied, so the resolved set is limited to true runtime
    dependencies regardless of what else is installed in the local
    environment (dev-only packages such as ``pytest`` are never reachable
    from the runtime graph). Each queue item carries the extras active on
    the parent so ``extra == "..."`` markers resolve correctly.
    """
    queue: deque[tuple[str, frozenset[str]]] = deque()
    visited: set[tuple[str, frozenset[str]]] = set()
    resolved: dict[str, Distribution] = {}

    for req in _read_project_dependencies():
        name = _normalize(req.name)
        extras = frozenset(req.extras)
        key = (name, extras)
        if key not in visited:
            visited.add(key)
            queue.append(key)

    while queue:
        name, active_extras = queue.popleft()
        dist = installed.get(name)
        if dist is None:
            continue
        resolved[name] = dist
        for req_str in dist.requires or []:
            req = Requirement(req_str)
            child_name = _normalize(req.name)
            if child_name in EXCLUDED_PACKAGES:
                continue
            if not _requirement_active(req, active_extras):
                continue
            child_extras = frozenset(req.extras)
            key = (child_name, child_extras)
            if key not in visited:
                visited.add(key)
                queue.append(key)

    return resolved


def _normalize_license(raw: str) -> str:
    """Map a raw license string to a canonical short form.

    Wheels/installs frequently emit the same license under slightly different
    strings ("MIT" vs "MIT License", "BSD License" vs "BSD-3-Clause", etc.).
    Normalizing keeps the lock stable across platforms and packaging tools.
    """
    key = raw.strip().lower()
    if not key:
        return "UNKNOWN"
    if key in LICENSE_NORMALIZATION:
        return LICENSE_NORMALIZATION[key]
    return raw.strip()


def _extract_license(dist: Distribution) -> str:
    name = _normalize(dist.metadata["Name"])
    if name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[name]

    # PEP 639 License-Expression is the preferred modern field.
    expr = dist.metadata.get("License-Expression")
    if expr:
        return _normalize_license(expr)

    classifiers = dist.metadata.get_all("Classifier") or []
    license_classifiers = [c for c in classifiers if c.startswith("License ::")]
    if license_classifiers:
        # Take the most specific (last) classifier, mirroring pip-licenses behavior.
        return _normalize_license(license_classifiers[-1].split("::")[-1].strip())

    raw = (dist.metadata.get("License") or "").strip()
    if raw and "\n" not in raw and len(raw) < 80:
        return _normalize_license(raw)

    return "UNKNOWN"


def _extract_homepage(name: str, version: str) -> str:
    # Deterministic across platforms: always point at the canonical PyPI
    # project page so we don't drift on Home-page / Project-URL field order
    # or presence (which can vary between wheels and packaging tools).
    return f"https://pypi.org/project/{name}/{version}/"


def _license_text_url(license_name: str, homepage: str) -> str | None:
    if license_name in LICENSE_TEXT_URLS:
        return LICENSE_TEXT_URLS[license_name]
    return homepage if "pypi.org" not in homepage else None


def _build_lock_entry(dist: Distribution) -> dict[str, Any]:
    name = dist.metadata["Name"]
    version = dist.version
    license_name = _extract_license(dist)
    homepage = _extract_homepage(name, version)
    return {
        "name": name,
        "version": version,
        "license": license_name,
        "homepage": homepage,
        "licenseTextUrl": _license_text_url(license_name, homepage),
    }


def generate_lock() -> list[dict[str, Any]]:
    installed = _installed_distributions()
    resolved = _resolve_packages(installed)
    entries = [_build_lock_entry(dist) for dist in resolved.values()]
    return sorted(entries, key=lambda e: _normalize(e["name"]))


def _print_lock_diff(existing: str, generated: str) -> None:
    """Print a concise diff between the committed and generated locks."""
    try:
        old = {e["name"]: e for e in json.loads(existing)}
        new = {e["name"]: e for e in json.loads(generated)}
    except json.JSONDecodeError:
        return
    for name in sorted(set(old) | set(new)):
        if name not in old:
            print(f"  + {name} {new[name]['version']}", file=sys.stderr)
        elif name not in new:
            print(f"  - {name} {old[name]['version']}", file=sys.stderr)
        elif old[name] != new[name]:
            print(f"  ~ {name}: {old[name]} -> {new[name]}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate runtime dependency license baseline")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("licenses.lock.json"),
        help="Output path for the license lock",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if the committed lock is out of date",
    )
    args = parser.parse_args()

    lock = generate_lock()
    content = json.dumps(lock, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        existing = args.output.read_text(encoding="utf-8")
        if existing != content:
            print(f"License lock is out of date: {args.output}", file=sys.stderr)
            print("Run scripts/generate_license_lock.py to regenerate it.", file=sys.stderr)
            _print_lock_diff(existing, content)
            return 1
        print("License lock is up to date.")
        return 0

    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote {args.output} with {len(lock)} runtime dependencies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

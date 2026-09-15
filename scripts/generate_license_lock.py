from __future__ import annotations

import argparse
import json
import sys
import tomllib
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


EXCLUDED_PACKAGES = {
    _normalize(name)
    for name in {
        "corpus-kb",
        "python-magic-bin",
        "pywin32",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "syrupy",
        "coverage",
    }
}

LICENSE_TEXT_URLS = {
    "mit": "https://opensource.org/licenses/MIT",
    "mit license": "https://opensource.org/licenses/MIT",
    "bsd": "https://opensource.org/licenses/BSD-3-Clause",
    "bsd license": "https://opensource.org/licenses/BSD-3-Clause",
    "bsd-2-clause": "https://opensource.org/licenses/BSD-2-Clause",
    "bsd-3-clause": "https://opensource.org/licenses/BSD-3-Clause",
    "apache software license": "https://opensource.org/licenses/Apache-2.0",
    "apache-2.0": "https://opensource.org/licenses/Apache-2.0",
    "apache license 2.0": "https://opensource.org/licenses/Apache-2.0",
    "isc license (iscl)": "https://opensource.org/licenses/ISC",
    "isc": "https://opensource.org/licenses/ISC",
    "mozilla public license 2.0 (mpl 2.0)": "https://opensource.org/licenses/MPL-2.0",
    "mpl-2.0": "https://opensource.org/licenses/MPL-2.0",
    "python software foundation license": "https://docs.python.org/3/license.html#psf-license",
    "psf-2.0": "https://docs.python.org/3/license.html#psf-license",
}


def _read_runtime_deps(pyproject_path: Path) -> list[str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return list(data.get("project", {}).get("dependencies", []))


def _installed_distributions() -> dict[str, Distribution]:
    return {_normalize(dist.metadata["Name"]): dist for dist in distributions()}


def _resolve_packages(
    root_reqs: list[str],
    installed: dict[str, Distribution],
) -> dict[str, Distribution]:
    try:
        from packaging.requirements import Requirement
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("packaging is required for dependency resolution") from exc

    resolved: dict[str, Distribution] = {}
    pending = [_normalize(Requirement(req).name) for req in root_reqs]
    visited = set()

    while pending:
        name = pending.pop()
        if name in visited or name in EXCLUDED_PACKAGES:
            continue
        visited.add(name)
        dist = installed.get(name)
        if dist is None:
            continue
        resolved[name] = dist
        for req_str in dist.requires or []:
            req = Requirement(req_str)
            req_name = _normalize(req.name)
            if req_name in EXCLUDED_PACKAGES:
                continue
            if req.marker is not None and not req.marker.evaluate():
                continue
            pending.append(req_name)

    return resolved


def _extract_license(dist: Distribution) -> str:
    # PEP 639 License-Expression is the preferred modern field.
    expr = dist.metadata.get("License-Expression")
    if expr:
        return expr.strip()

    classifiers = dist.metadata.get_all("Classifier") or []
    license_classifiers = [c for c in classifiers if c.startswith("License ::")]
    if license_classifiers:
        # Take the most specific (last) classifier, mirroring pip-licenses behavior.
        return license_classifiers[-1].split("::")[-1].strip()

    raw = (dist.metadata.get("License") or "").strip()
    if raw and "\n" not in raw and len(raw) < 80:
        return raw

    return "UNKNOWN"


def _extract_homepage(dist: Distribution, name: str, version: str) -> str:
    home = dist.metadata.get("Home-page")
    if home and home.lower() not in {"unknown", ""}:
        return home

    project_urls = dist.metadata.get_all("Project-URL") or []
    for url in project_urls:
        if "homepage" in url.lower() or "source" in url.lower():
            _, _, link = url.partition(",")
            link = link.strip()
            if link:
                return link
    if project_urls:
        _, _, link = project_urls[0].partition(",")
        link = link.strip()
        if link:
            return link

    return f"https://pypi.org/project/{name}/{version}/"


def _license_text_url(license_name: str, homepage: str) -> str | None:
    key = license_name.lower()
    if key in LICENSE_TEXT_URLS:
        return LICENSE_TEXT_URLS[key]
    return homepage if "pypi.org" not in homepage else None


def _build_lock_entry(dist: Distribution) -> dict[str, Any]:
    name = dist.metadata["Name"]
    version = dist.version
    license_name = _extract_license(dist)
    homepage = _extract_homepage(dist, name, version)
    return {
        "name": name,
        "version": version,
        "license": license_name,
        "homepage": homepage,
        "licenseTextUrl": _license_text_url(license_name, homepage),
    }


def generate_lock(pyproject_path: Path) -> list[dict[str, Any]]:
    root_reqs = _read_runtime_deps(pyproject_path)
    installed = _installed_distributions()
    resolved = _resolve_packages(root_reqs, installed)
    entries = [_build_lock_entry(dist) for dist in resolved.values()]
    return sorted(entries, key=lambda e: _normalize(e["name"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate runtime dependency license baseline")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml",
    )
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

    lock = generate_lock(args.pyproject)
    content = json.dumps(lock, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        existing = args.output.read_text(encoding="utf-8")
        if existing != content:
            print(f"License lock is out of date: {args.output}", file=sys.stderr)
            print("Run scripts/generate_license_lock.py to regenerate it.", file=sys.stderr)
            return 1
        print("License lock is up to date.")
        return 0

    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote {args.output} with {len(lock)} runtime dependencies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Structural gate for .claude-plugin/marketplace.json (issue 331, M3).

This repository is the plugin marketplace (owner directive 2026-11-05).
The gate checks the manifest structure against the marketplace reference
at code.claude.com/docs/en/plugins/marketplace-reference (verified
2026-11-05), and the version lockstep between each manifest entry and
that entry's resolved plugin.json.

Runs in the plugin-manifest CI job through `poe plugin-manifest`.
Python 3.11, stdlib only. Exit 0 is pass.

Structural checks always run. The lockstep cross-checks activate per
entry once that entry's resolved plugin.json exists (issue 331 step 1
subtree import); while no entry resolves, the gate prints one note line
and passes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / ".claude-plugin" / "marketplace.json"

# Marketplace reference, "Top-level fields": letters, digits, ".", "_",
# and "-", start with a letter or digit, and no "..". The same rule
# holds for a plugin entry name ("Plugin entries"). fullmatch guards
# against trailing newlines, which "$" would accept.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")

# Marketplace reference, "Plugin sources": required fields per source
# kind. A source object without its required fields would parse in the
# manifest and fail later at plugin load; the gate fails it here.
REQUIRED_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "github": ("repo",),
    "url": ("url",),
    "git-subdir": ("url", "path"),
    "npm": ("package",),
    "archive": ("url", "sha256"),
    "command": ("command",),
}
SOURCE_KINDS = frozenset(REQUIRED_SOURCE_FIELDS)

# Marketplace reference, "Reserved names" (the subset a gate can pin
# without chasing Claude Code versions): session/built-in marketplace
# names, package-manager names reserved in any casing, and the plugin
# test name.
RESERVED_MARKETPLACE_NAMES = {
    "inline",
    "builtin",
    "skills-dir",
    "synced",
    "claude-plugin-test",
    "npm",
    "pip",
    "uv",
    "cargo",
    "github",
    "gh",
}


def fail(messages: list[str], text: str) -> None:
    messages.append(text)


def check_name(messages: list[str], name: object, where: str) -> None:
    if not isinstance(name, str) or not name:
        fail(messages, f"{where} name is missing or empty")
        return
    if " " in name:
        fail(messages, f"{where} name {name!r} has a space; use kebab-case")
    if not NAME_RE.fullmatch(name):
        fail(messages, f"{where} name {name!r} has a bad character or start")
    if ".." in name:
        fail(messages, f"{where} name {name!r} holds '..'")
    if where == "marketplace" and name.lower() in RESERVED_MARKETPLACE_NAMES:
        fail(messages, f"marketplace name {name!r} is reserved")


def check_source(messages: list[str], source: object, prefix: str) -> None:
    if isinstance(source, str):
        if not source.startswith("./"):
            fail(messages, f"{prefix} source {source!r} must start with './'")
            return
        if ".." in source:
            fail(messages, f"{prefix} source {source!r} escapes the marketplace root")
        if "\\" in source:
            fail(messages, f"{prefix} source {source!r} uses a backslash; use '/'")
        return
    if isinstance(source, dict):
        kind = source.get("source")
        if kind not in SOURCE_KINDS:
            fail(messages, f"{prefix} source type {kind!r} is not one of {sorted(SOURCE_KINDS)}")
            return
        assert isinstance(kind, str)
        for field in REQUIRED_SOURCE_FIELDS[kind]:
            value = source.get(field)
            if not isinstance(value, str) or not value:
                fail(messages, f"{prefix} {kind} source is missing required field {field!r}")
        return
    fail(messages, f"{prefix} source must be a './' path string or a source object")


def check_entry(messages: list[str], entry: object, index: int, seen: set[str]) -> None:
    prefix = f"plugins[{index}]"
    if not isinstance(entry, dict):
        fail(messages, f"{prefix} is not an object")
        return
    check_name(messages, entry.get("name"), "plugin")
    name = entry.get("name")
    if isinstance(name, str):
        if name in seen:
            fail(messages, f"duplicate plugin name {name!r}")
        seen.add(name)
    check_source(messages, entry.get("source"), prefix)
    if not isinstance(entry.get("description"), str) or not entry.get("description"):
        fail(messages, f"{prefix} has no description")
    version = entry.get("version")
    if version is not None and (not isinstance(version, str) or not SEMVER_RE.fullmatch(version)):
        fail(messages, f"{prefix} version {version!r} is not semver")


def resolved_plugin_json(entry: object) -> Path | None:
    """Return the entry's local plugin.json path, when it is knowable.

    Only a relative-path source resolves inside this repository. An
    object source (github, url, git-subdir, ...) fetches from elsewhere,
    so there is nothing local to lockstep against.
    """
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    if not isinstance(source, str) or not source.startswith("./"):
        return None
    return REPO / source[2:] / ".claude-plugin" / "plugin.json"


def cross_check_plugin(messages: list[str], manifest: dict[str, object]) -> bool:
    """Check every entry against its resolved plugin.json.

    True when at least one entry resolved to an existing plugin.json.
    False when none did (plugin tree not imported yet).
    """
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return False
    ran = False
    for entry in entries:
        plugin_json = resolved_plugin_json(entry)
        if plugin_json is None or not plugin_json.is_file():
            continue
        ran = True
        if not isinstance(entry, dict):
            continue
        try:
            plugin = json.loads(plugin_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(messages, f"{plugin_json} does not parse: {exc}")
            continue
        if not isinstance(plugin, dict):
            fail(messages, f"{plugin_json} is not an object")
            continue
        plugin_name = plugin.get("name")
        entry_name = entry.get("name")
        if plugin_name != entry_name:
            fail(messages, f"{plugin_json} name {plugin_name!r} != entry name {entry_name!r}")
        plugin_version = plugin.get("version")
        entry_version = entry.get("version")
        if not isinstance(entry_version, str) or not entry_version:
            fail(
                messages,
                f"entry {entry_name!r} has no version but {plugin_json} exists; "
                "set the entry version to match",
            )
        elif plugin_version != entry_version:
            fail(
                messages,
                f"{plugin_json} version {plugin_version!r} != entry version {entry_version!r}; "
                "bump both in the same PR",
            )
    return ran


def main() -> int:
    messages: list[str] = []
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL {MANIFEST_PATH}: {exc}")
        return 1
    if not isinstance(manifest, dict):
        print("FAIL marketplace.json is not an object")
        return 1

    check_name(messages, manifest.get("name"), "marketplace")
    owner = manifest.get("owner")
    if (
        not isinstance(owner, dict)
        or not isinstance(owner.get("name"), str)
        or not owner.get("name")
    ):
        fail(messages, "owner.name is missing or empty")
    if not isinstance(manifest.get("description"), str) or not manifest.get("description"):
        fail(messages, "marketplace has no description")

    plugins = manifest.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        fail(messages, "plugins is missing or empty")
    else:
        seen: set[str] = set()
        for index, entry in enumerate(plugins):
            check_entry(messages, entry, index, seen)

    ran = cross_check_plugin(messages, manifest)

    if messages:
        for line in messages:
            print(f"FAIL {line}")
        return 1
    if ran:
        print("ok: marketplace.json valid and in lockstep with plugin.json")
    else:
        print("ok: marketplace.json valid (plugin tree not imported yet; lockstep check pending)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

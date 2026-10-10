"""Release comparison (v6 §6 ``compare``) — pure diff of two manifests.

Codes are keyed by NAME (the stable human handle across versions). A removed
code paired with an added code carrying the SAME definition is reported as
merged/renamed rather than remove+add. Definition changes compare definition,
inclusion, and exclusion verbatim; method/threshold changes are the profile
and method_declaration key diffs; metric deltas are numeric key deltas (B - A,
rounded to the manifest's metric precision).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from corpus_kb.research.release.manifest import METRIC_PRECISION, ReleaseManifest

_CODE_FIELDS = ("definition", "inclusion", "exclusion")


@dataclass(frozen=True)
class ReleaseDiff:
    """Structured diff between release A (old) and release B (new)."""

    codes_added: list[str] = field(default_factory=list)
    codes_removed: list[str] = field(default_factory=list)
    codes_merged: list[dict[str, str]] = field(default_factory=list)
    definitions_changed: list[dict[str, str]] = field(default_factory=list)
    method_changes: dict[str, dict[str, object]] = field(default_factory=dict)
    threshold_changes: dict[str, dict[str, object]] = field(default_factory=dict)
    metric_deltas: dict[str, float] = field(default_factory=dict)
    identical: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "codes_added": self.codes_added,
            "codes_removed": self.codes_removed,
            "codes_merged": self.codes_merged,
            "definitions_changed": self.definitions_changed,
            "method_changes": self.method_changes,
            "threshold_changes": self.threshold_changes,
            "metric_deltas": self.metric_deltas,
            "identical": self.identical,
        }


def _codes_by_name(manifest: ReleaseManifest) -> dict[str, dict[str, object]]:
    return {str(code.get("name", "")): dict(code) for code in manifest.codes}


def compare_releases(old: ReleaseManifest, new: ReleaseManifest) -> ReleaseDiff:
    """Diff two release manifests; ``identical`` iff hashes' inputs match."""
    old_codes = _codes_by_name(old)
    new_codes = _codes_by_name(new)
    diff = ReleaseDiff()
    if old.sha256() == new.sha256():
        return diff
    diff.identical = False

    added_names = set(new_codes) - set(old_codes)
    removed_names = set(old_codes) - set(new_codes)
    # merged/renamed: a removed code's definition reappears under a new name
    for removed in sorted(removed_names):
        definition = str(old_codes[removed].get("definition", ""))
        successor = next(
            (
                a
                for a in sorted(added_names)
                if str(new_codes[a].get("definition", "")) == definition
            ),
            None,
        )
        if successor is not None:
            diff.codes_merged.append({"from": removed, "to": successor})
            added_names.discard(successor)
            removed_names.discard(removed)
    diff.codes_added = sorted(added_names)
    diff.codes_removed = sorted(removed_names)

    for name in sorted(set(old_codes) & set(new_codes)):
        changes = {
            field_name: str(new_codes[name].get(field_name, ""))
            for field_name in _CODE_FIELDS
            if old_codes[name].get(field_name) != new_codes[name].get(field_name)
        }
        if changes:
            diff.definitions_changed.append({"code": name, **changes})

    diff.method_changes = _key_changes(old.method_declaration, new.method_declaration)
    diff.threshold_changes = _key_changes(old.profile, new.profile)
    old_metrics = _numeric_metrics(old.metric_snapshots)
    new_metrics = _numeric_metrics(new.metric_snapshots)
    for key in sorted(set(old_metrics) & set(new_metrics)):
        delta = round(new_metrics[key] - old_metrics[key], METRIC_PRECISION)
        if delta != 0:
            diff.metric_deltas[key] = delta
    return diff


def _key_changes(old: dict[str, object], new: dict[str, object]) -> dict[str, dict[str, object]]:
    changes: dict[str, dict[str, object]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changes[key] = {"from": old.get(key), "to": new.get(key)}
    return changes


def _numeric_metrics(snapshots: dict[str, object]) -> dict[str, float]:
    flat: dict[str, float] = {}

    def walk(prefix: str, node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            flat[prefix] = float(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else str(key), value)

    walk("", snapshots)
    return flat

"""Migration name-set cross-check for the schema-parity gate (issue #230).

The state fold diffs the final schema STATE, so the same feature name at
a different ordinal stays invisible. From 030 on, every DuckDB file runs
one ordinal above its Postgres twin: pg ``030_extraction_cache.sql`` is
duckdb ``031_extraction_cache.sql``. Applied databases record ordinals,
so renumbering is not an option. This module therefore compares name
sets and never ordinals:

- Forward files: ``031_statements.sql`` carries the name ``statements``.
- Rollback files: ``rollback_012_coding_schema.sql`` carries the name
  ``coding_schema``.

The same name at a different ordinal passes by design. A name present on
one side only is undeclared drift, unless ``ALLOWED_NAME_ASYMMETRY``
declares it. A stale allowlist row also fails, so the list stays honest.
Imported by ``schema_parity_gate.py``; no CLI entry of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PG_MIGRATIONS = Path("src/corpus_kb/migrations")
DUCK_MIGRATIONS = Path("src/corpus_kb/migrations_duckdb")

_FORWARD = re.compile(r"^\d+_(.+)\.sql$")
_ROLLBACK = re.compile(r"^rollback_\d+_(.+)\.sql$")

# Declared one-sided names: (kind, side, name) -> one-line why. ``side``
# is the side that holds the file. A row with no live asymmetry is STALE
# and fails the gate, so this list cannot outlive its drift.
ALLOWED_NAME_ASYMMETRY: dict[tuple[str, str, str], str] = {
    # -- Forward: DuckDB-only files.
    ("forward", "duckdb", "base_schema"): (
        "DuckDB mirrors the Postgres base as 000_base_schema. The Postgres"
        " base lives in storage/schema.sql, not in a numbered file."
    ),
    # -- Forward: the one deliberate gap in the offset zone (issue #230).
    # -- It has no rollback file on either side, so the rollback compare
    # -- needs no row for it.
    ("forward", "duckdb", "ingest_runs_source"): (
        "DuckDB-only ledger migration (030). No Postgres twin, and no"
        " rollback in either tree (issue #230)."
    ),
}


@dataclass(frozen=True)
class NameDrift:
    """One one-sided, duplicated, or unclassifiable migration file name."""

    kind: str  # "forward", "rollback", "duplicate", or "unclassified"
    side: str  # the side that holds the file: "pg" or "duckdb"
    name: str
    file: str  # repo-relative path of the file on that side

    def line(self) -> str:
        return f"{self.kind} name {self.name!r} on {self.side} only: {self.file}"


@dataclass
class SideScan:
    """Bare migration names of one dialect directory."""

    forward: dict[str, str] = field(default_factory=dict)
    rollback: dict[str, str] = field(default_factory=dict)
    problems: list[NameDrift] = field(default_factory=list)


@dataclass
class NameSetResult:
    """Name-set compare outcome: sets, declared rows, drift, stale rows."""

    pg_forward: list[str] = field(default_factory=list)
    duck_forward: list[str] = field(default_factory=list)
    pg_rollback: list[str] = field(default_factory=list)
    duck_rollback: list[str] = field(default_factory=list)
    declared: list[tuple[NameDrift, str]] = field(default_factory=list)
    undeclared: list[NameDrift] = field(default_factory=list)
    stale: list[tuple[tuple[str, str, str], str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.undeclared and not self.stale


def migration_name(filename: str) -> tuple[str, str] | None:
    """Return (kind, bare name) for one migration file name, else None."""
    match = _FORWARD.match(filename)
    if match:
        return "forward", match.group(1)
    match = _ROLLBACK.match(filename)
    if match:
        return "rollback", match.group(1)
    return None


def _scan_dir(directory: Path, root: Path, side: str) -> SideScan:
    """Map bare names to repo-relative files for one migration directory."""
    scan = SideScan()
    for path in sorted(directory.glob("*.sql")):
        rel = path.relative_to(root).as_posix()
        parsed = migration_name(path.name)
        if parsed is None:
            scan.problems.append(NameDrift("unclassified", side, path.name, rel))
            continue
        kind, name = parsed
        target = scan.forward if kind == "forward" else scan.rollback
        if name in target:
            scan.problems.append(NameDrift("duplicate", side, name, rel))
            continue
        target[name] = rel
    return scan


def name_set_result(
    root: Path, allowlist: dict[tuple[str, str, str], str] = ALLOWED_NAME_ASYMMETRY
) -> NameSetResult:
    """Compare pg and duckdb migration name sets; split declared drift."""
    pg = _scan_dir(root / PG_MIGRATIONS, root, "pg")
    duck = _scan_dir(root / DUCK_MIGRATIONS, root, "duckdb")
    result = NameSetResult(
        pg_forward=sorted(pg.forward),
        duck_forward=sorted(duck.forward),
        pg_rollback=sorted(pg.rollback),
        duck_rollback=sorted(duck.rollback),
    )
    drifts = [*pg.problems, *duck.problems]
    for kind, pg_map, duck_map in (
        ("forward", pg.forward, duck.forward),
        ("rollback", pg.rollback, duck.rollback),
    ):
        for name in sorted(pg_map.keys() - duck_map.keys()):
            drifts.append(NameDrift(kind, "pg", name, pg_map[name]))
        for name in sorted(duck_map.keys() - pg_map.keys()):
            drifts.append(NameDrift(kind, "duckdb", name, duck_map[name]))
    seen: set[tuple[str, str, str]] = set()
    for drift in drifts:
        key = (drift.kind, drift.side, drift.name)
        why = allowlist.get(key)
        # Duplicates and unclassified files fail outright; the allowlist
        # is only for one-sided names.
        if why is None or drift.kind in {"duplicate", "unclassified"}:
            result.undeclared.append(drift)
        else:
            seen.add(key)
            result.declared.append((drift, why))
    for key, why in allowlist.items():
        if key not in seen:
            result.stale.append((key, why))
    result.undeclared.sort(key=lambda d: (d.kind, d.side, d.name))
    return result

"""Config-backend parity gate (parity program PR-9, zero-drift rule 12).

Every config leaf in ``get_default_config()`` AND the packaged
``config.yaml`` must be declared in ``corpus_kb.config_backend_matrix``
with backends, behavior, and reader. The gate fails in BOTH directions:
a default leaf with no row is undeclared, and a row naming no default
leaf is stale. Each row's reader file must contain the row's grep token
(default: the dotted key) — the folded-in dead-key check (rule 12: one
gate, no separate dead-key gate). A refusal row (behavior names a
refusal, or the reader is ``backend_gates.py``) must also bind to a test
in ``tests/test_backend_gates.py``: its ``bind`` token must appear there,
so declarations and typed refusals cannot drift apart.

Exit 0 = parity; exit 1 = one FAIL line per violation. Stdlib only plus
the two modules it gates (``corpus_kb.config``,
``corpus_kb.config_backend_matrix``) and PyYAML — a runtime dependency
the checked code itself uses. Run it through poe (rule 8):
``poe config-backend-parity``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from corpus_kb.config_backend_matrix import ConfigBackendRow, matrix_rows

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "corpus_kb"
PACKAGED_YAML = PACKAGE_DIR / "config.yaml"
BACKEND_GATES_READER = "backend_gates.py"
BACKEND_GATES_TESTS = ROOT / "tests" / "test_backend_gates.py"
KNOWN_BACKENDS = frozenset({"postgres", "duckdb"})


def collect_leaves(tree: Mapping[str, object], prefix: str = "") -> set[str]:
    """Dotted leaf paths of a config tree (dicts recurse; others are leaves)."""
    leaves: set[str] = set()
    for key, value in tree.items():
        path = f"{prefix}{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            leaves |= collect_leaves(value, f"{path}.")
        else:
            leaves.add(path)
    return leaves


def _refusal_row(row: ConfigBackendRow) -> bool:
    """True when the row declares a refusal (behavior or reader says so)."""
    return "refused" in row.behavior or row.reader == BACKEND_GATES_READER


def violations(
    rows: list[ConfigBackendRow],
    defaults_leaves: set[str],
    packaged_leaves: set[str],
    reader_texts: dict[str, str],
    tests_text: str,
) -> list[str]:
    """One FAIL line per violation; empty list = parity.

    Pure: the caller supplies the file texts, so tests can mutate them
    in-process (the break-once proofs).
    """
    fails: list[str] = []
    declared = [row.key for row in rows]
    dupes = sorted({key for key in declared if declared.count(key) > 1})
    for key in dupes:
        fails.append(f"FAIL: {key} is declared more than once in the matrix")
    for source, leaves in (
        ("get_default_config", defaults_leaves),
        ("packaged config.yaml", packaged_leaves),
    ):
        for key in sorted(leaves - set(declared)):
            fails.append(f"FAIL: {key} exists in {source} but has no matrix row (undeclared leaf)")
        for key in sorted(set(declared) - leaves):
            fails.append(f"FAIL: {key} is declared in the matrix but absent from {source}")
    for row in rows:
        if set(row.backends) - KNOWN_BACKENDS:
            fails.append(f"FAIL: {row.key} names an unknown backend in {row.backends}")
        text = reader_texts.get(row.reader)
        if text is None:
            fails.append(f"FAIL: {row.key} names reader {row.reader} but that file does not exist")
            continue
        token = row.token or row.key
        if token not in text:
            fails.append(
                f"FAIL: {row.key} names reader {row.reader} but the reader has no "
                f"'{token}' (dead key or wrong reader)"
            )
        if not _refusal_row(row):
            continue
        if row.reader != BACKEND_GATES_READER:
            fails.append(
                f"FAIL: {row.key} declares a refusal but its reader is not {BACKEND_GATES_READER}"
            )
        if not row.bind:
            fails.append(f"FAIL: {row.key} declares a refusal but carries no bind token")
        elif row.bind not in tests_text:
            fails.append(
                f"FAIL: {row.key} binds to '{row.bind}' but tests/test_backend_gates.py "
                "does not mention it (refusal test removed or renamed)"
            )
    return fails


def _load_rows() -> list[ConfigBackendRow]:
    return list(matrix_rows())


def main() -> int:
    """Run the gate: import defaults, read the sources, report violations."""
    from corpus_kb.config import get_default_config

    rows = _load_rows()
    defaults_leaves = collect_leaves(get_default_config())
    packaged = yaml.safe_load(PACKAGED_YAML.read_text(encoding="utf-8"))
    packaged_leaves = collect_leaves(packaged if isinstance(packaged, Mapping) else {})
    readers = sorted({row.reader for row in rows})
    reader_texts: dict[str, str] = {}
    for reader in readers:
        path = PACKAGE_DIR / reader
        reader_texts[reader] = (
            path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        )
    tests_text = (
        BACKEND_GATES_TESTS.read_text(encoding="utf-8", errors="replace")
        if BACKEND_GATES_TESTS.exists()
        else ""
    )
    fails = violations(rows, defaults_leaves, packaged_leaves, reader_texts, tests_text)
    print(
        f"config-backend-parity: {len(rows)} rows, {len(defaults_leaves)} default leaves, "
        f"{len(packaged_leaves)} packaged leaves"
    )
    for fail in fails:
        print(fail)
    if fails:
        print(f"Config-backend parity: FAIL ({len(fails)} violation(s))")
        return 1
    print("Config-backend parity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""S4 spike 1: per-table tenancy audit of forward migrations.

Parses src/corpus_kb/migrations/0*.sql (forward only, no rollback_*),
extracts every CREATE TABLE statement with balanced parens, and reports
whether the table body declares a tenant_id column. Output: JSON manifest
on stdout (machine) + exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MIG_DIR = Path(__file__).resolve().parents[2] / "src" / "corpus_kb" / "migrations"


def strip_comments(sql: str) -> str:
    # Remove line comments then block comments (order matters for -- inside /* */).
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def extract_create_tables(sql: str) -> list[tuple[str, int]]:
    """Return [(table_name, lineno)] with balanced-paren CREATE TABLE bodies."""
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?", sql, re.I):
        start = m.end()
        name_m = re.match(r"[\w.\"']+", sql[start:])
        if not name_m:
            continue
        name = name_m.group(0).strip('"').replace("'", "")
        # Balanced parens scan for the body.
        depth = 0
        body_start = start + name_m.end()
        i = body_start
        while i < len(sql):
            c = sql[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = sql[body_start:i] if depth == 0 else sql[body_start:]
        lineno = sql[: m.start()].count("\n") + 1
        has_tenant = bool(re.search(r"\btenant_id\b", body, re.I))
        out.append((name, has_tenant, lineno))
    return out


def main() -> None:
    rows = []
    for path in sorted(MIG_DIR.glob("0*.sql")):
        sql = strip_comments(path.read_text(encoding="utf-8"))
        for name, has_tenant, lineno in extract_create_tables(sql):
            rows.append(
                {
                    "migration": path.name,
                    "table": name,
                    "tenant_id": has_tenant,
                    "line": lineno,
                }
            )
    # Dedupe (CREATE TABLE IF NOT EXISTS can repeat across migrations, e.g. 015 recreates).
    seen: dict[str, dict] = {}
    for r in rows:
        key = r["table"]
        if key in seen and seen[key]["tenant_id"] != r["tenant_id"]:
            r["note"] = f"conflicting tenant_id across files (also {seen[key]['migration']})"
        seen[key] = r
    manifest = {
        "tables_with_tenant_id": sorted(k for k, v in seen.items() if v["tenant_id"]),
        "tables_without_tenant_id": sorted(k for k, v in seen.items() if not v["tenant_id"]),
        "detail": rows,
    }
    json.dump(manifest, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

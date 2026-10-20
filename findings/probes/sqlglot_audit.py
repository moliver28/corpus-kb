"""S4 spike 5: sqlglot static audit of forward migrations.

For every forward migration (0*.sql), split into statements (dollar-quote
aware), parse with dialect postgres, and transpile postgres -> duckdb purely
as a FLAGGER. Records per-file construct families and parse/transpile
failures. NEVER used for production SQL generation.

Run with the throwaway venv: .venv-sqlglot/Scripts/python.exe
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import sqlglot
from sqlglot.errors import ParseError

MIG_DIR = Path(__file__).resolve().parents[2] / "src" / "corpus_kb" / "migrations"

_DOLLAR = re.compile(r"(\$[A-Za-z_]*\$)")


def split_statements(sql: str) -> list[str]:
    """Split on top-level ';' skipping $quoted$ blocks, '...' strings, and
    both comment styles (a ';' inside a comment must not split)."""
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        m = _DOLLAR.match(sql, i)
        if m:
            tag = m.group(1)
            end = sql.find(tag, m.end())
            if end == -1:
                buf.append(sql[i:])
                break
            buf.append(sql[i : end + len(tag)])
            i = end + len(tag)
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i : j + 1])
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j  # keep the newline, drop the comment
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def family(stmt: str) -> str:
    s = re.sub(r"\s+", " ", stmt.strip()).upper()
    m = re.match(
        r"(CREATE (?:OR REPLACE )?(?:UNIQUE )?[A-Z ]+?|ALTER TABLE [A-Z ]+?|"
        r"DROP (?:TRIGGER|POLICY|TABLE|INDEX|FUNCTION)[A-Z ]*?|SET|DO|COMMENT ON [A-Z]+|"
        r"INSERT INTO|SELECT|UPDATE|DELETE FROM|GRANT|REVOKE|LOCK TABLE)\b",
        s,
    )
    if not m:
        return s.split(" ")[0]
    fam = m.group(1).strip()
    # Collapse verbose heads.
    fam = re.sub(r"\s+(IF NOT EXISTS|IF EXISTS)", "", fam)
    fam = re.sub(r"\s+", " ", fam)
    return fam


# PG-specific constructs worth flagging (regex, case-insensitive).
FLAGS = {
    "index_method_hnsw": r"\bUSING\s+hnsw\b",
    "index_method_ivfflat": r"\bUSING\s+ivfflat\b",
    "index_method_gin": r"\bUSING\s+gin\b",
    "index_method_gist": r"\bUSING\s+gist\b",
    "index_method_btree": r"\bUSING\s+btree\b",
    "opclass": r"\b(?:halfvec|vector|tsvector|jsonb)_(?:cosine|l2|ip)_ops\b|\btsvector_ops\b",
    "rls_enable": r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b",
    "rls_force": r"\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b",
    "create_policy": r"\bCREATE\s+POLICY\b",
    "create_trigger": r"\bCREATE\s+TRIGGER\b",
    "plpgsql_do_block": r"^\s*DO\s*\$",
    "plpgsql_function": r"\bLANGUAGE\s+plpgsql\b",
    "guc_set": r"^SET\s+app\.",
    "gen_random_uuid": r"\bgen_random_uuid\s*\(",
    "current_setting": r"\bcurrent_setting\s*\(",
    "to_tsvector_family": r"\b(?:to_tsvector|plainto_tsquery|ts_rank|setweight|websearch_to_tsquery)\s*\(",
    "partition_by": r"\bPARTITION\s+BY\s+(?:LIST|RANGE|HASH)\b",
    "halfvec_type": r"\bhalfvec\s*\(\s*\d+\s*\)",
    "vector_type": r"\bvector\s*\(\s*\d+\s*\)",
    "replica_identity": r"\bREPLICA\s+IDENTITY\b",
    "create_extension": r"\bCREATE\s+EXTENSION\b",
    "create_graph_age": r"\bcreate_graph\s*\(|\bag_catalog\b|\bag_graph\b",
    "age_load": r"\bLOAD\s+'age'\b",
    "pgml": r"\bpgml\.",
    "uuid_ossp": r"\buuid-ossp\b|\buuid_generate_v4\b",
    "deferrable": r"\bDEFERRABLE\b",
    "exclude_constraint": r"\bEXCLUDE\s+USING\b",
    "materialized_view": r"\bMATERIALIZED\s+VIEW\b",
    "create_schema": r"\bCREATE\s+SCHEMA\b",
}


def main() -> None:
    report: dict[str, object] = {}
    global_fails: Counter[str] = Counter()
    global_flag_counts: Counter[str] = Counter()

    for path in sorted(MIG_DIR.glob("0*.sql")):
        sql = path.read_text(encoding="utf-8")
        stmts = split_statements(sql)
        per_file: dict[str, object] = {"statements": len(stmts)}
        fam_counts: Counter[str] = Counter(family(s) for s in stmts)
        per_file["families"] = dict(fam_counts)

        parse_fails: list[dict[str, str]] = []
        transpile_fails: list[dict[str, str]] = []
        flag_hits: Counter[str] = Counter()
        for s in stmts:
            for name, rx in FLAGS.items():
                if re.search(rx, s, re.I | re.M):
                    flag_hits[name] += 1
                    global_flag_counts[name] += 1
            try:
                exprs = sqlglot.parse_one(s, read="postgres")
            except ParseError as e:
                parse_fails.append({"family": family(s), "error": str(e)[:160]})
                global_fails[f"parse:{family(s)}"] += 1
                continue
            except Exception as e:  # noqa: BLE001
                parse_fails.append({"family": family(s), "error": f"{type(e).__name__}: {e}"[:160]})
                global_fails[f"parse-other:{family(s)}"] += 1
                continue
            try:
                sqlglot.transpile(s, read="postgres", write="duckdb")
            except ParseError as e:
                transpile_fails.append({"family": family(s), "error": str(e)[:160]})
                global_fails[f"transpile:{family(s)}"] += 1
            except Exception as e:  # noqa: BLE001
                transpile_fails.append({"family": family(s), "error": f"{type(e).__name__}: {e}"[:160]})
                global_fails[f"transpile-other:{family(s)}"] += 1
            _ = exprs
        per_file["parse_failures"] = parse_fails
        per_file["transpile_failures"] = transpile_fails
        per_file["pg_construct_flags"] = dict(flag_hits)
        report[path.name] = per_file

    out = {
        "sqlglot_version": sqlglot.__version__,
        "per_file": report,
        "global_failure_families": dict(global_fails),
        "global_construct_flags": dict(global_flag_counts),
    }
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

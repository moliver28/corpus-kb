"""Fold a migration set into one final per-table schema state (sqlglot).

Folds every forward CREATE/ALTER statement into the state of one logical
database; schema_parity_compare diffs two states. sqlglot is STATIC
ANALYSIS ONLY here (storage portability program, OSS table). DO-block
bodies get a best-effort inner fold: plain ALTER TABLE statements are
parsed and applied; EXECUTE-format dynamic SQL stays out of reach and
shows up as drift unless the gate allowlist declares it. Column,
constraint, and ALTER-action folding live in schema_parity_alter.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import sqlglot
from schema_parity_alter import fold_alter, fold_column, fold_table_constraints
from schema_parity_model import (
    IndexSpec,
    Origin,
    SchemaState,
    SequenceSpec,
    TableSpec,
)
from schema_parity_sql import SchemaParityError, split_statements
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

logging.getLogger("sqlglot").setLevel(logging.CRITICAL)

_INNER_ALTER = re.compile(r"\bALTER\s+TABLE[^;]+;", re.IGNORECASE)


def _table_of_create(node: exp.Create) -> str:
    target = node.args["this"]
    table = target.this if isinstance(target, exp.Schema) else target
    return table.name if isinstance(table, exp.Table) else ""


def _fold_create(node: exp.Create, state: SchemaState, at: Origin) -> None:
    if node.kind == "TABLE":
        table = TableSpec(name=_table_of_create(node), origin=at)
        for col in node.find_all(exp.ColumnDef):
            fold_column(table, col, at)
        fold_table_constraints(node, table)
        state.tables[table.name] = table
    elif node.kind == "SEQUENCE":
        named = node.find(exp.Table)
        name = named.name if named else ""
        state.sequences[name] = SequenceSpec(name=name, origin=at)


def _plain_index(node: exp.Create, at: Origin) -> IndexSpec | None:
    """Classify one CREATE INDEX. Returns None for PG-only search indexes.

    PG-only roles (dropped on DuckDB, declared in the gate allowlist):
    GIN/HNSW/IVFFLAT and friends (USING <non-btree>) and expression
    indexes (a function call in the column list, e.g. to_tsvector).

    Partial indexes (a WHERE clause) fold as plain indexes on the same
    columns: the predicate shrinks the indexed row set, not the role.
    A UNIQUE index additionally lands in its table's unique set, so a
    unique index on one side matches a unique constraint on the other
    (DuckDB cannot ADD CONSTRAINT; pg 014 uses the constraint form).
    """
    idx = node.args["this"]
    if re.search(r"USING\s+(?!BTREE\b)(\w+)", node.sql().upper()):
        return None
    columns: list[str] = []
    params = idx.args.get("params")
    for item in (params.args.get("columns") or []) if params is not None else []:
        col = item.this if isinstance(item, exp.Ordered) else item
        if not isinstance(col, exp.Column):
            return None
        columns.append(col.name)
    unique = bool(node.args.get("unique"))
    return IndexSpec(
        table=idx.args["table"].name,
        columns=tuple(sorted(columns)),
        unique=unique,
        origin=at,
    )


def _fold_do_body(text: str, dialect: str, state: SchemaState, at: Origin) -> None:
    """Best-effort fold of plain ALTER TABLE statements inside a DO block.

    pg 008 and 014 add named UNIQUE constraints inside DO bodies. The
    statements are plain SQL, so each one is parsed and applied. Bodies
    that build SQL with EXECUTE format(...) cannot fold statically; their
    effect is declared in the gate allowlist instead.
    """
    for match in _INNER_ALTER.finditer(text):
        try:
            inner = sqlglot.parse_one(match.group(0), read=dialect)
        except (ParseError, TokenError):
            # Best-effort by contract: EXECUTE-format bodies never parse
            # statically (their effect lives in the gate allowlist). The
            # tokenizer raises TokenError on the %I placeholders — same
            # skip, not a gate failure.
            continue
        if isinstance(inner, exp.Alter):
            fold_alter(inner, state, at)


def _index_name(node: exp.Create) -> str:
    """The declared name of a CREATE INDEX statement."""
    idx = node.args["this"]
    name = idx.args.get("this")
    return name.name if name is not None else ""


def _fold_drop(node: exp.Drop, state: SchemaState, at: Origin) -> None:
    """Fold DROP TABLE / DROP INDEX (the 040/041 rebuild-and-drop files).

    DROP TABLE removes the table and every index that targets it; DROP
    INDEX removes the named index from the role map and the name map.
    Other DROP kinds (SCHEMA, FUNCTION, ...) carry no folded state. The
    targets sit in ``tables`` (sqlglot 30.x); ``expressions`` is the
    fallback shape.
    """
    kind = (node.args.get("kind") or "").upper()
    targets = node.args.get("tables") or node.args.get("expressions") or []
    for target in targets:
        if kind == "TABLE":
            name = target.name
            if name:
                state.drop_table(name)
        elif kind == "INDEX":
            name = target.name
            if name:
                state.drop_index(name)
            else:
                print(f"Schema parity fold: {at.where()}: DROP INDEX without a name; skipped.")


def fold_file(path: Path, dialect: str, display: str, state: SchemaState) -> SchemaState:
    """Fold one SQL file into the shared state, in place.

    ALTER statements must see tables created by earlier files, so the
    state accumulates across files of one migration set. Raises
    SchemaParityError on unparsable SQL.
    """
    sql = path.read_text(encoding="utf-8")
    for offset, text in split_statements(sql):
        if not text.strip():
            continue
        at = Origin(file=display, line=sql.count("\n", 0, offset) + 1)
        try:
            parsed = sqlglot.parse(text, read=dialect)
        except ParseError as err:
            raise SchemaParityError(f"{at.where()}: sqlglot failed: {err}") from err
        for node in parsed:
            if node is None:
                continue
            if isinstance(node, exp.Create) and node.kind == "INDEX":
                spec = _plain_index(node, at)
                if spec is None:
                    continue
                state.indexes[spec.role] = spec
                state.index_names[_index_name(node)] = spec
                target = state.tables.get(spec.table) if spec.unique else None
                if target is not None:
                    target.uniques.add(spec.columns)
            elif isinstance(node, exp.Create):
                _fold_create(node, state, at)
            elif isinstance(node, exp.Alter):
                fold_alter(node, state, at)
            elif isinstance(node, exp.Drop):
                _fold_drop(node, state, at)
            elif isinstance(node, exp.Command) and text.lstrip()[:2].upper() == "DO":
                _fold_do_body(text, dialect, state, at)
    # pg SERIAL/IDENTITY imply sequences without a CREATE SEQUENCE
    # statement; register them so both sides compare by name.
    for table in state.tables.values():
        for seq in table.sequences.values():
            state.sequences.setdefault(seq, SequenceSpec(name=seq, origin=table.origin))
    return state

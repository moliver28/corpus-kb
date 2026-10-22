"""Fold a migration set into one final per-table schema state (sqlglot).

Folds every forward CREATE/ALTER statement into the state of one logical
database; schema_parity_compare diffs two states. sqlglot is STATIC
ANALYSIS ONLY here (storage portability program, OSS table). DO-block
bodies get a best-effort inner fold: plain ALTER TABLE statements are
parsed and applied; EXECUTE-format dynamic SQL stays out of reach and
shows up as drift unless the gate allowlist declares it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import sqlglot
from schema_parity_model import (
    ColumnSpec,
    IndexSpec,
    Origin,
    SchemaState,
    SequenceSpec,
    TableSpec,
    normalize_default,
    normalize_type,
)
from schema_parity_sql import SchemaParityError, split_statements
from sqlglot import exp
from sqlglot.errors import ParseError

logging.getLogger("sqlglot").setLevel(logging.CRITICAL)

_NOT_NULL_CONSTRAINT = re.compile(r"^(.+)_not_null$", re.IGNORECASE)
_INNER_ALTER = re.compile(r"\bALTER\s+TABLE[^;]+;", re.IGNORECASE)


def _schema_cols(node: exp.Expression) -> tuple[str, ...]:
    """Column tuple of a PRIMARY KEY / UNIQUE constraint (Schema of names)."""
    this = node.args.get("this")
    if isinstance(this, exp.Schema):
        return tuple(e.name for e in this.expressions)
    return ()


def _ref_target(ref: exp.Expression) -> tuple[str, tuple[str, ...]]:
    """(table, cols) of a REFERENCES clause; table may be Schema-wrapped."""
    this = ref.args.get("this")
    table = this.this if isinstance(this, exp.Schema) else this
    cols = tuple(e.name for e in ref.args.get("expressions") or [])
    return (table.name if isinstance(table, exp.Table) else "", cols)


def _fold_column(table: TableSpec, col: exp.ColumnDef, at: Origin) -> None:
    name = col.name
    raw_type = col.args["kind"].sql() if col.args.get("kind") else "UNKNOWN"
    nullable = True
    default = ""
    sequences: list[str] = []
    for con in col.args.get("constraints") or []:
        kind = con.kind
        if isinstance(kind, exp.NotNullColumnConstraint):
            nullable = False
        elif isinstance(kind, exp.DefaultColumnConstraint):
            default = normalize_default(kind.this.sql())
            match = re.search(r"nextval\('([^']+)'", default)
            if match:
                # A nextval default IS the sequence backing; it is compared
                # as a sequence, not as a default (pg serial has none).
                default = ""
                sequences.append(match.group(1))
        elif isinstance(kind, exp.GeneratedAsIdentityColumnConstraint):
            sequences.append(f"{table.name}_{name}_seq")
        elif isinstance(kind, exp.Reference):
            table.fks[(name,)] = _ref_target(kind)
    if raw_type.strip().upper() in ("SERIAL", "BIGSERIAL", "SMALLSERIAL"):
        # pg SERIAL implies a <table>_<col>_seq sequence behind the int.
        sequences.append(f"{table.name}_{name}_seq")
    table.columns[name] = ColumnSpec(
        type=normalize_type(raw_type), nullable=nullable, default=default, origin=at
    )
    for seq in sequences:
        table.sequences[name] = seq


def _fold_table_constraints(node: exp.Expression, table: TableSpec) -> None:
    """Fold PK / UNIQUE / FK constraints found anywhere inside node."""
    for pk in node.find_all(exp.PrimaryKey):
        table.primary_key = _schema_cols(pk)
    for uq in node.find_all(exp.UniqueColumnConstraint):
        table.uniques.add(tuple(sorted(_schema_cols(uq))))
    for fk in node.find_all(exp.ForeignKey):
        target = _ref_target(fk.args["reference"])
        cols = tuple(sorted(e.name for e in fk.args.get("expressions") or []))
        table.fks[cols] = target


def _table_of_create(node: exp.Create) -> str:
    target = node.args["this"]
    table = target.this if isinstance(target, exp.Schema) else target
    return table.name if isinstance(table, exp.Table) else ""


def _fold_create(node: exp.Create, state: SchemaState, at: Origin) -> None:
    if node.kind == "TABLE":
        table = TableSpec(name=_table_of_create(node), origin=at)
        for col in node.find_all(exp.ColumnDef):
            _fold_column(table, col, at)
        _fold_table_constraints(node, table)
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


def _fold_alter(node: exp.Alter, state: SchemaState, at: Origin) -> None:
    named = node.find(exp.Table)
    table = state.tables.get(named.name if named else "")
    if table is None:
        raise SchemaParityError(f"{at.where()}: ALTER has no folded CREATE TABLE")
    for action in node.args.get("actions") or []:
        if isinstance(action, exp.ColumnDef):
            _fold_column(table, action, at)
        elif isinstance(action, exp.AlterColumn):
            target = action.args.get("this")
            col_name = target.name if target is not None else ""
            if action.args.get("dtype") is not None and col_name in table.columns:
                table.columns[col_name].type = normalize_type(action.args["dtype"].sql())
                table.columns[col_name].origin = at
            elif action.args.get("allow_null") is not None and col_name in table.columns:
                table.columns[col_name].nullable = bool(action.args["allow_null"])
                table.columns[col_name].origin = at
        elif isinstance(action, exp.RenameColumn):
            old, new = action.args["this"].name, action.args["to"].name
            if old in table.columns:
                table.columns[new] = table.columns.pop(old)
                table.columns[new].origin = at
        elif isinstance(action, exp.Drop):
            for dropped in action.args.get("expressions") or []:
                dropped_name = dropped.name
                if action.args.get("kind") == "COLUMN" and dropped_name in table.columns:
                    del table.columns[dropped_name]
                elif match := _NOT_NULL_CONSTRAINT.match(dropped_name):
                    col_name = match.group(1)
                    if col_name in table.columns:
                        # House convention: <col>_not_null names a NOT NULL
                        # constraint; dropping it clears the column flag.
                        table.columns[col_name].nullable = True
                        table.columns[col_name].origin = at
        else:
            # ADD CONSTRAINT (AddConstraint) and friends: the constraint
            # nodes sit under the action, wherever the dialect put them.
            _fold_table_constraints(action, table)


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
        except ParseError:
            continue
        if isinstance(inner, exp.Alter):
            _fold_alter(inner, state, at)


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
                target = state.tables.get(spec.table) if spec.unique else None
                if target is not None:
                    target.uniques.add(spec.columns)
            elif isinstance(node, exp.Create):
                _fold_create(node, state, at)
            elif isinstance(node, exp.Alter):
                _fold_alter(node, state, at)
            elif isinstance(node, exp.Command) and text.lstrip()[:2].upper() == "DO":
                _fold_do_body(text, dialect, state, at)
    # pg SERIAL/IDENTITY imply sequences without a CREATE SEQUENCE
    # statement; register them so both sides compare by name.
    for table in state.tables.values():
        for seq in table.sequences.values():
            state.sequences.setdefault(seq, SequenceSpec(name=seq, origin=table.origin))
    return state

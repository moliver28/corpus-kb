"""Fold column definitions and ALTER TABLE actions into a TableSpec.

Shared by schema_parity_fold: CREATE TABLE columns, in-table constraints,
and every ALTER TABLE action shape (add column, alter column, rename
column, drop column/constraint, add constraint). sqlglot is STATIC
ANALYSIS ONLY here (storage portability program, OSS table).
"""

from __future__ import annotations

import re

from schema_parity_model import (
    ColumnSpec,
    Origin,
    SchemaState,
    TableSpec,
    normalize_default,
    normalize_type,
)
from schema_parity_sql import SchemaParityError
from sqlglot import exp

_NOT_NULL_CONSTRAINT = re.compile(r"^(.+)_not_null$", re.IGNORECASE)


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


def fold_column(table: TableSpec, col: exp.ColumnDef, at: Origin) -> None:
    """Fold one column definition (type, nullability, default, FK, sequences)."""
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


def fold_table_constraints(node: exp.Expression, table: TableSpec) -> None:
    """Fold PK / UNIQUE / FK constraints found anywhere inside node."""
    for pk in node.find_all(exp.PrimaryKey):
        table.primary_key = _schema_cols(pk)
    for uq in node.find_all(exp.UniqueColumnConstraint):
        table.uniques.add(tuple(sorted(_schema_cols(uq))))
    for fk in node.find_all(exp.ForeignKey):
        target = _ref_target(fk.args["reference"])
        cols = tuple(sorted(e.name for e in fk.args.get("expressions") or []))
        table.fks[cols] = target


def fold_alter(node: exp.Alter, state: SchemaState, at: Origin) -> None:
    """Apply one ALTER TABLE to its folded table.

    Raises SchemaParityError when the ALTER names a table no earlier file
    created (state accumulates across files in fold order).
    """
    named = node.find(exp.Table)
    table = state.tables.get(named.name if named else "")
    if table is None:
        raise SchemaParityError(f"{at.where()}: ALTER has no folded CREATE TABLE")
    for action in node.args.get("actions") or []:
        _fold_alter_action(action, table, at)


def _fold_alter_action(action: exp.Expression, table: TableSpec, at: Origin) -> None:
    """Dispatch one ALTER action to its fold helper."""
    if isinstance(action, exp.ColumnDef):
        fold_column(table, action, at)
    elif isinstance(action, exp.AlterColumn):
        _alter_column(action, table, at)
    elif isinstance(action, exp.RenameColumn):
        _rename_column(action, table, at)
    elif isinstance(action, exp.Drop):
        _drop_action(action, table, at)
    else:
        # ADD CONSTRAINT (AddConstraint) and friends: the constraint
        # nodes sit under the action, wherever the dialect put them.
        fold_table_constraints(action, table)


def _alter_column(action: exp.AlterColumn, table: TableSpec, at: Origin) -> None:
    """Fold ALTER COLUMN ... TYPE / DROP NOT NULL + SET NOT NULL shapes."""
    target = action.args.get("this")
    col_name = target.name if target is not None else ""
    column = table.columns.get(col_name)
    if column is None:
        return
    if action.args.get("dtype") is not None:
        column.type = normalize_type(action.args["dtype"].sql())
        column.origin = at
    elif action.args.get("allow_null") is not None:
        column.nullable = bool(action.args["allow_null"])
        column.origin = at


def _rename_column(action: exp.RenameColumn, table: TableSpec, at: Origin) -> None:
    """Fold ALTER TABLE ... RENAME COLUMN (keeps the column's spec)."""
    old, new = action.args["this"].name, action.args["to"].name
    if old in table.columns:
        table.columns[new] = table.columns.pop(old)
        table.columns[new].origin = at


def _drop_action(action: exp.Drop, table: TableSpec, at: Origin) -> None:
    """Fold DROP COLUMN and DROP of a named ``<col>_not_null`` constraint."""
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

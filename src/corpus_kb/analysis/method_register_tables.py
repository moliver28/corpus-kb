"""Table-contract schema and validation for the analysis method register.

The novice-UX program (wave 2) adds one optional ``output_table`` block to
each ``MethodRecord`` row: the machine-checked contract for the uniform
rows the method's op returns (column names and types, the result order
with its tiebreaker, the rounding precision, the envelope denominators,
and the honesty caveats). The nested schema models and the content checks
both live here, so one module owns the whole table contract;
``method_register`` imports the model and calls
:func:`validate_table_fields` per row. Ops that answer envelope-only
(counts, composed report sections) declare no table.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

COLUMN_TYPES = (
    "text",
    "id",
    "integer",
    "number",
    "boolean",
    "id_list",
    "object_list",
)

#: Reserved order entry: the rows come back in the producing model's own
#: reply order (method 07). It must stand alone — a model rank is total.
MODEL_RANK = "model_rank"

MAX_COLUMNS = 12
MAX_PRECISION = 12
MAX_CAVEATS = 5


class TableColumn(BaseModel):
    """One declared column: name, machine type, description, unit."""

    model_config = {"extra": "forbid"}

    name: str
    type: str
    description: str
    unit: str | None = None


class OutputTable(BaseModel):
    """The declared contract for one method's result rows."""

    model_config = {"extra": "forbid"}

    columns: list[TableColumn]
    order_by: list[str]
    ties: str
    precision: int | None = None
    denominators: list[str] | None = None
    caveats: list[str] | None = None


class TableRecord(Protocol):
    """The table-field surface the checks read."""

    output_table: OutputTable | None


def validate_table_fields(record: TableRecord) -> None:
    """Validate one row's ``output_table``. ``None`` always passes."""
    table = record.output_table
    if table is None:
        return
    _check_columns(table)
    _check_order(table)
    if not table.ties.strip():
        raise ValueError("output_table.ties: name the tiebreaker rule in one line.")
    if table.precision is not None and (
        isinstance(table.precision, bool) or not 0 <= table.precision <= MAX_PRECISION
    ):
        raise ValueError(
            f"output_table.precision: {table.precision} is out of range. "
            f"Use 0 to {MAX_PRECISION} decimals."
        )
    _check_list(table.denominators, "denominators")
    _check_list(table.caveats, "caveats")


def _check_columns(table: OutputTable) -> None:
    """Columns: 1 to 12, unique non-empty names, known types, clean text."""
    if not 1 <= len(table.columns) <= MAX_COLUMNS:
        raise ValueError(
            f"output_table.columns: {len(table.columns)} found. List 1 to {MAX_COLUMNS}."
        )
    names: list[str] = []
    for position, column in enumerate(table.columns):
        where = f"output_table.columns[{position}]"
        if not column.name.strip():
            raise ValueError(f"{where}.name: must be non-empty text.")
        if column.type not in COLUMN_TYPES:
            raise ValueError(
                f"{where}.type: {column.type!r} is not supported. "
                f"Use one of: {', '.join(COLUMN_TYPES)}."
            )
        if not column.description.strip():
            raise ValueError(f"{where}.description: must be non-empty text.")
        if column.unit is not None and not column.unit.strip():
            raise ValueError(f"{where}.unit: must be non-empty text when present.")
        names.append(column.name)
    if len(set(names)) != len(names):
        raise ValueError("output_table.columns: remove duplicate column names.")


def _check_order(table: OutputTable) -> None:
    """Order entries name declared columns; model_rank stands alone."""
    if not table.order_by:
        raise ValueError("output_table.order_by: list at least one order field.")
    if MODEL_RANK in table.order_by and len(table.order_by) > 1:
        raise ValueError(f"output_table.order_by: {MODEL_RANK} is total and must stand alone.")
    names = {column.name for column in table.columns}
    seen: set[str] = set()
    for key in table.order_by:
        field = key[1:] if key.startswith("-") else key
        if field != MODEL_RANK and field not in names:
            raise ValueError(
                f"output_table.order_by: {field} is not a declared column. "
                f"Declared: {', '.join(sorted(names))}."
            )
        if field in seen:
            raise ValueError(f"output_table.order_by: remove the duplicate {field}.")
        seen.add(field)


def _check_list(values: list[str] | None, field_name: str) -> None:
    """Optional string lists: non-empty unique entries."""
    if not values:
        return
    where = f"output_table.{field_name}"
    if any(not value.strip() for value in values):
        raise ValueError(f"{where}: every entry must be non-empty text.")
    if len(set(values)) != len(values):
        raise ValueError(f"{where}: remove duplicate entries.")
    if field_name == "caveats" and len(values) > MAX_CAVEATS:
        raise ValueError(f"{where}: {len(values)} found. List up to {MAX_CAVEATS}.")

"""Diff two folded schema states for the schema-parity gate.

Deterministic order everywhere (zero-drift rule 9): tables, columns,
constraints, and indexes all compare in sorted order, so a harness can
snapshot the report.
"""

from __future__ import annotations

from dataclasses import dataclass

from schema_parity_model import SchemaState, TableSpec


@dataclass(frozen=True)
class Drift:
    """One difference between the two folded states.

    key is the allowlist lookup: (kind, table, item). table is empty for
    set-wide facts (top-level sequences).
    """

    kind: str
    table: str
    item: str
    pg_note: str
    duck_note: str
    pg_at: str
    duck_at: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.table, self.item)

    def line(self) -> str:
        subject = f"{self.table}.{self.item}" if self.table else self.item
        return (
            f"{self.kind} {subject}: pg={self.pg_note} "
            f"({self.pg_at}); duckdb={self.duck_note} ({self.duck_at})"
        )


def compare_states(pg: SchemaState, duck: SchemaState) -> list[Drift]:
    """Diff the two folded states. Deterministic order (rule 9)."""
    out: list[Drift] = []
    for name in sorted(set(pg.tables) | set(duck.tables)):
        p, d = pg.tables.get(name), duck.tables.get(name)
        if p is None or d is None:
            holder = p or d
            assert holder is not None
            out.append(
                Drift(
                    "table",
                    name,
                    "",
                    "present" if p else "absent",
                    "present" if d else "absent",
                    holder.origin.where(),
                    "",
                )
            )
            continue
        _compare_table(name, p, d, out)
    for role in sorted(set(pg.indexes) | set(duck.indexes)):
        if role[2]:  # unique indexes compare through the per-table unique set
            continue
        pi, di = pg.indexes.get(role), duck.indexes.get(role)
        if pi is not None and di is not None:
            continue
        holder = pi or di
        assert holder is not None
        out.append(
            Drift(
                "index",
                role[0],
                ",".join(role[1]),
                "present" if pi else "absent",
                "present" if di else "absent",
                holder.origin.where(),
                "",
            )
        )
    for seq in sorted(set(pg.sequences) | set(duck.sequences)):
        if seq in pg.sequences and seq in duck.sequences:
            continue
        holder = pg.sequences.get(seq) or duck.sequences.get(seq)
        assert holder is not None
        out.append(
            Drift(
                "sequence",
                "",
                seq,
                "present" if seq in pg.sequences else "absent",
                "present" if seq in duck.sequences else "absent",
                holder.origin.where(),
                "",
            )
        )
    return out


def _compare_table(name: str, p: TableSpec, d: TableSpec, out: list[Drift]) -> None:
    """Column, PK, unique, FK, and sequence comparison for one table."""
    for col in sorted(set(p.columns) | set(d.columns)):
        pc, dc = p.columns.get(col), d.columns.get(col)
        if pc is None or dc is None:
            holder = pc or dc
            assert holder is not None
            out.append(
                Drift(
                    "column",
                    name,
                    col,
                    pc.type if pc else "absent",
                    dc.type if dc else "absent",
                    pc.origin.where() if pc else "",
                    dc.origin.where() if dc else "",
                )
            )
            continue
        if pc.type != dc.type:
            out.append(
                Drift(
                    "column.type", name, col, pc.type, dc.type, pc.origin.where(), dc.origin.where()
                )
            )
        if pc.nullable != dc.nullable:
            out.append(
                Drift(
                    "column.nullable",
                    name,
                    col,
                    "not null" if not pc.nullable else "null",
                    "not null" if not dc.nullable else "null",
                    pc.origin.where(),
                    dc.origin.where(),
                )
            )
        if pc.default != dc.default:
            out.append(
                Drift(
                    "column.default",
                    name,
                    col,
                    pc.default or "none",
                    dc.default or "none",
                    pc.origin.where(),
                    dc.origin.where(),
                )
            )
    if p.primary_key != d.primary_key:
        out.append(
            Drift(
                "pk",
                name,
                ",".join(p.primary_key) or "none",
                ",".join(p.primary_key) or "none",
                ",".join(d.primary_key) or "none",
                p.origin.where(),
                d.origin.where(),
            )
        )
    for uq in sorted(set(p.uniques) | set(d.uniques)):
        if uq in p.uniques and uq in d.uniques:
            continue
        out.append(
            Drift(
                "unique",
                name,
                ",".join(uq),
                ",".join(uq) if uq in p.uniques else "absent",
                ",".join(uq) if uq in d.uniques else "absent",
                p.origin.where(),
                d.origin.where(),
            )
        )
    for fk in sorted(set(p.fks) | set(d.fks)):
        if p.fks.get(fk) == d.fks.get(fk):
            continue
        out.append(
            Drift(
                "fk",
                name,
                ",".join(fk),
                str(p.fks.get(fk, "absent")),
                str(d.fks.get(fk, "absent")),
                p.origin.where(),
                d.origin.where(),
            )
        )
    for col in sorted(set(p.sequences) | set(d.sequences)):
        ps, ds = p.sequences.get(col, ""), d.sequences.get(col, "")
        if ps != ds:
            out.append(
                Drift(
                    "sequence",
                    name,
                    col,
                    ps or "absent",
                    ds or "absent",
                    p.origin.where(),
                    d.origin.where(),
                )
            )

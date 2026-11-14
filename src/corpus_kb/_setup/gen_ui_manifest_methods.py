"""Method-guidance rows for the generated UI manifest artifacts.

The op manifest is op-shaped, so the register's progression view needs
its own generated artifact: ``ui_src/src/api/methods.generated.ts``. The
SPA's progression rail reads that table and stays pure data — no method
name, rung, concept, or link is hard-coded in a component.

Source (zero-drift rule 1: one source per table): the analysis method
register (``config/analysis_methods.yaml``), loaded through the one
register loader so the schema checks run on every generation.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb.analysis.method_register_loader import load_method_register

REGISTER_REL = Path("config") / "analysis_methods.yaml"

# The row keys the generated TypeScript table carries. The link lists
# normalize to ``[]`` (the register's ``None`` means "no links");
# ``difficulty`` and ``op`` stay ``None``-capable so the SPA can stay
# honest about methods without them (human-led rows have no op).


def build_method_entries(project_root: Path) -> list[dict[str, object]]:
    """One guidance row per register method, sorted by method id."""
    register_path = project_root / REGISTER_REL
    register = load_method_register({"analysis": {"method_register_path": str(register_path)}})
    entries: list[dict[str, object]] = []
    for record in register.methods:
        entries.append(
            {
                "id": record.id,
                "name": record.name,
                "summary": record.summary,
                "op": record.op,
                "difficulty": record.difficulty,
                "concepts": list(record.concepts or []),
                "prerequisites": list(record.prerequisites or []),
                "next": list(record.next or []),
            }
        )
    entries.sort(key=lambda entry: str(entry["id"]))
    return entries

"""Conflicts: the contradiction cascade (Living Corpus W3).

``conflicts.cascade`` — Gates A/B (temporal, structured swaps), pure.
``conflicts.scan`` — the driver plus the Gate C NLI judge seam.
``conflicts.store`` — flag-don't-delete persistence + resolve path.
"""

from __future__ import annotations

from .cascade import CascadeVerdict, StatementRow, entity_swap, gate_a, gate_b, relation_swap
from .scan import nli_judge, scan_statements
from .store import ConflictStore, conflict_row

__all__ = [
    "CascadeVerdict",
    "ConflictStore",
    "StatementRow",
    "conflict_row",
    "entity_swap",
    "gate_a",
    "gate_b",
    "nli_judge",
    "relation_swap",
    "scan_statements",
]

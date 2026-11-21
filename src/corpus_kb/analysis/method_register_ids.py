"""The shared method-id ledger for the register family.

One home for ``EXPECTED_IDS`` so the model validator, the loader, and
the dataflow checks read ONE count — the upper bound stays data-driven,
never a second hard-coded total.
"""

from __future__ import annotations

EXPECTED_IDS = frozenset(f"{index:02d}" for index in range(1, 34))
EXPECTED_MAX_ID = max(int(value) for value in EXPECTED_IDS)

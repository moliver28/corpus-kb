"""The demo-load operation row (momus F8 close-out, 2026-11-13).

One row: the bundled sample-corpus loader. ``load_demo_kb`` IS the ONE
handler every surface routes through (the CLI ``demo-kb`` command, the
MCP ``demo_load`` tool, and POST /api/demo/load), so the row declares
all three surfaces and no socket surface — the registry row
``demo-load`` declares the same set. The row ends the gap where the
registry declared the surface but no catalogue row locked its envelope
(the #415 ``degraded`` key shipped declared only in a docstring).

All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.envelopes import DEMO_ENVELOPE
from corpus_kb.contracts.model import CLI, HTTP, MCP, Operation, SurfaceSpec

DEMO_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="demo_load",
        family="demo",
        description=(
            "Load the bundled sample knowledge corpus through the standard ingest pipeline."
        ),
        handler="corpus_kb.demo_kb:load_demo_kb",
        envelope_keys=DEMO_ENVELOPE,
        # The conditional receipt keys ride INSIDE ``result`` (the
        # ask-family shape), so the top-level key set is the same on
        # every run and no http/socket extra is declared. DEMO_KEYS
        # names them; the parity gate pins the receipt to that set.
        surfaces=(
            SurfaceSpec(MCP, "demo_load"),
            SurfaceSpec(CLI, "demo-kb"),
            SurfaceSpec(HTTP, "/api/demo/load", "POST"),
        ),
        exit_codes=(0, 1),
    ),
)

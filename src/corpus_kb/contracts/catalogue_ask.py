"""The operation catalogue: the ask family (UX program Wave 1).

``ask`` is the first-class grounded-Q&A operation: hybrid retrieval,
temperature-0 generation with [n] citations, sentence-level citation
filtering, and optional claim verification. Honest statuses: ``success``,
``empty`` (no evidence), ``degraded`` (generation down; evidence only).
Registry-first rule: change the row, then the surfaces, then the gates.
All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

# Guaranteed-on-success keys: the SAME top-level set the search family
# carries (the result body's answer/sentences/sources/verify/model/note
# are outcome-stable and live under "result").
ASK_ENVELOPE = ("status", "result", "degraded_excluded")

ASK_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="ask",
        family="ask",
        description="Answer a question from retrieved evidence with cited sentences.",
        handler="corpus_kb.tools.ask_tools:ask",
        params=(
            ParamSpec("query", required=True),
            ParamSpec("k"),
            _TENANT,
        ),
        envelope_keys=ASK_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "ask"),
            SurfaceSpec(CLI, "ask"),
            SurfaceSpec(HTTP, "/api/ask", "POST"),
            SurfaceSpec(SOCKET, "ask"),
        ),
        exit_codes=(0, 1, 3),
    ),
)

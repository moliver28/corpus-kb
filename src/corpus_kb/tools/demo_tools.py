"""Research-demo MCP tool: the narrated pipeline in ONE machine envelope.

Full-surface parity wave: ``research_demo`` was CLI-only by design; the
wave makes every operation reachable on every surface, so this module
wraps the SAME ``research.demo.run_demo`` entry point the CLI calls. The
demo narrates to stdout; the tool captures that narration into the
envelope instead of leaking it to the server log. Long-running and
synchronous: one call runs the full pipeline and returns when it ends.
"""

from __future__ import annotations

import contextlib
import io

__all__ = ["research_demo"]

# The demo exit codes run_demo documents (see docs/OPERATIONS.md): 0 the
# pipeline finished, 2 embeddings unavailable at preflight, 1 any other
# failure caught by the demo's own error narration.
EXIT_OK = 0
EXIT_UNAVAILABLE = 2


async def research_demo(
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Run the narrated demo pipeline; return a machine-readable receipt.

    Calls ``research.demo.run_demo`` — the CLI's own entry point — under a
    stdout capture, so the narration lands in the envelope instead of the
    caller's terminal. The demo manages its own database connection from
    the config (the pool is not injected). Long-running; synchronous.

    Returns ``{"status", "exit_code", "narration"}``. ``status`` maps the
    exit code: ``completed`` (0), ``unavailable`` (2), ``failed`` (other).
    """
    from corpus_kb.research.demo import run_demo

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = await run_demo(config if config is not None else None)
    status = "completed" if exit_code == EXIT_OK else "failed"
    if exit_code == EXIT_UNAVAILABLE:
        status = "unavailable"
    return {"status": status, "exit_code": int(exit_code), "narration": buffer.getvalue()}

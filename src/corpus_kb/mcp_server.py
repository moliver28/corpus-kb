"""The MCP stdio server: the editor-facing surface, registry-generated.

``build_mcp_server`` turns the parity catalogue (``corpus_kb.contracts``)
and ``surface_registry`` into one live MCP server. The catalogue is the
single source: a new row with ``SurfaceSpec(MCP, ...)`` automatically
gains an MCP tool — description, parameter schema, and envelope come from
the row and the shared function it names, never from a hand-written
wrapper here. Registration mechanics live in ``mcp_tools.py``.

Every tool wraps the ONE shared function the CLI, HTTP, and socket
surfaces already route through (program standard 1). Pool and config are
injected at build time; the shared tool layer is imported lazily, so this
module's import cost stays at stdlib + asyncpg + the ``mcp`` SDK.

SDK note (verified against the installed ``mcp`` 2.3.0): the official
server class is ``mcp.server.mcpserver.MCPServer`` — the 1.x name
``mcp.server.fastmcp.FastMCP`` was renamed in the 2.x major (the old
module now raises with a pointer to the migration guide). stdio serving
is ``MCPServer.run_stdio_async()`` inside the caller's event loop, so the
asyncpg pool and the protocol share one loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import cast

import asyncpg
from mcp.server.mcpserver import MCPServer

from corpus_kb.errors import ConfigError, DatabaseUnavailableError

logger = logging.getLogger(__name__)

# ASD-STE100: short active sentences. The editor reads this before any call.
SERVER_INSTRUCTIONS = (
    "Search and ingest a local knowledge base. "
    "Ingest text, files, directories, and web pages. "
    "Ask grounded questions and list or delete documents. "
    "Release tools follow the governed release lifecycle."
)


def build_mcp_server(pg_pool: asyncpg.Pool, config: dict[str, object] | None = None) -> MCPServer:
    """Build the MCP server over one shared pool.

    Every catalogue operation with an MCP surface, plus every
    ``surface_registry`` research/coding row, becomes a tool. Registration
    fails loudly on registry drift (a row without a function, a catalogue
    parameter the shared function does not take).
    """
    from corpus_kb.mcp_tools import register_catalogue_tools, register_registry_tools
    from corpus_kb.surface_registry import SERVER_DESCRIPTION, SERVER_NAME

    server = MCPServer(
        name=SERVER_NAME,
        description=SERVER_DESCRIPTION,
        instructions=SERVER_INSTRUCTIONS,
    )
    registered = register_catalogue_tools(server, pg_pool, config)
    registered += register_registry_tools(server, pg_pool, config, tuple(registered))
    logger.info("MCP server built with %d tools", len(registered))
    return server


async def _serve_stdio() -> None:
    """One event loop for the pool and the protocol: config, pool, serve."""
    from corpus_kb.config import load_config

    cfg = load_config()
    db = cast(dict[str, object], cfg.get("database") or {})
    conn_str = str(db.get("connection_string", ""))
    if not conn_str:
        raise ConfigError(
            "No database connection string. Set CORPUS_KB_DATABASE_URL or "
            "configure database.connection_string in config.yaml."
        )
    try:
        pool = await asyncpg.create_pool(conn_str)
    except Exception as exc:
        raise DatabaseUnavailableError(
            f"Failed to connect to Postgres at {conn_str}: {exc}",
            remediation="Run corpus-kb setup. Check the DSN and credentials.",
        ) from exc
    try:
        server = build_mcp_server(pool, cfg)
        logger.info("Serving MCP tools over stdio")
        await server.run_stdio_async()
    finally:
        await pool.close()
        logger.info("MCP stdio server stopped; pool closed")


def run_stdio_server() -> int:
    """Serve the MCP tools over stdio; block until the client disconnects.

    Returns 0 on a clean shutdown, 3 on a config or database failure (the
    error taxonomy's CLI exit code). All server output goes to stderr;
    stdout carries only the MCP wire protocol.
    """
    try:
        asyncio.run(_serve_stdio())
    except KeyboardInterrupt:
        logger.info("MCP stdio server interrupted")
    except (ConfigError, DatabaseUnavailableError) as exc:
        print(f"corpus-kb: {exc}", file=sys.stderr)
        print(f"corpus-kb: {exc.remediation}", file=sys.stderr)
        return exc.cli_exit
    return 0


__all__ = ["build_mcp_server", "run_stdio_server"]

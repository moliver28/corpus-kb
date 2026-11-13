"""The one corpus read tool the chat agent may call: ``mcp__corpus__search``.

NOT a new op ([D1]): the handler calls the SAME ``tools/search_tools.search``
coroutine the HTTP/socket surfaces call, so no catalogue row, no registry
row, no ``poe gen`` — the SDK MCP server runs inside the server process
and the browser never calls it. The wrapper adds nothing: ``k`` rides the
search tool's own checks, and the result order is the SearchEnvelope's
stable order.

Everything from ``claude_agent_sdk`` imports lazily — base installs
never import it, mirroring the ``[code]`` extra pattern.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import asyncpg

from corpus_kb.chat.events import ChatCitation

if TYPE_CHECKING:
    from claude_agent_sdk import SdkMcpTool
    from claude_agent_sdk.types import McpSdkServerConfig

__all__ = [
    "build_corpus_mcp_server",
    "build_corpus_tool",
    "citations_from_contract",
    "citations_from_sources",
]

_CORPUS_SERVER_NAME = "corpus"
_TOOL_NAME = "search"
_TOOL_DESCRIPTION = (
    "Search the user's corpus. Returns the top chunks with ids, titles, "
    "snippets, and scores. Read-only."
)
# The JSON Schema form (the simple type mapping cannot express an optional
# key); the SDK reference documents this shape for @tool.
_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "k": {"type": "integer", "minimum": 1},
    },
    "required": ["query"],
}


def _citation(row: dict[str, object]) -> ChatCitation:
    score = row.get("score")
    return ChatCitation(
        chunk_id=str(row.get("chunk_id", "")),
        document_id=str(row.get("doc_id", "")),
        title=str(row.get("source", "")),
        source_path=str(row.get("file_path") or ""),
        snippet=str(row.get("text", "")),
        score=float(score) if isinstance(score, (int, float)) else 0.0,
    )


def citations_from_contract(contract: dict[str, object]) -> list[ChatCitation]:
    """Citation chips from the tool's output contract."""
    rows = contract.get("results")
    if not isinstance(rows, list):
        return []
    return [_citation(row) for row in rows if isinstance(row, dict)]


def citations_from_sources(sources: list[dict[str, object]]) -> list[ChatCitation]:
    """Citation chips from the ask pipeline's evidence blocks."""
    return [_citation(source) for source in sources]


def build_corpus_tool(pg_pool: asyncpg.Pool, config: dict[str, object] | None) -> SdkMcpTool[Any]:
    """The decorated ``SdkMcpTool``; the SDK imports lazily."""
    from claude_agent_sdk import ToolAnnotations, tool

    async def handler(args: dict[str, Any]) -> dict[str, object]:
        return await _search_handler(pg_pool, config, args)

    return tool(
        _TOOL_NAME,
        _TOOL_DESCRIPTION,
        _SEARCH_INPUT_SCHEMA,
        annotations=ToolAnnotations(readOnlyHint=True),
    )(handler)


def build_corpus_mcp_server(
    pg_pool: asyncpg.Pool, config: dict[str, object] | None
) -> McpSdkServerConfig:
    """The in-process MCP server holding the one read tool."""
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(_CORPUS_SERVER_NAME, tools=[build_corpus_tool(pg_pool, config)])


async def _search_handler(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    args: dict[str, Any],
) -> dict[str, object]:
    """One corpus search: the same call POST /api/search makes.

    A validation failure from the search path surfaces as an ``is_error``
    tool result — the model reads the message and retries; the turn never
    dies on a bad argument.
    """
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools import search_tools

    query = str(args.get("query", "")).strip()
    k_raw = args.get("k")
    k = k_raw if isinstance(k_raw, int) and not isinstance(k_raw, bool) else None
    try:
        payload = await search_tools.search(pg_pool, query, config=config, k=k)
    except CorpusError as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": json.dumps(_contract(payload))}]}


def _contract(payload: dict[str, object]) -> dict[str, object]:
    """The tool's output contract: what the citation chips render."""
    rows_raw = payload.get("result")
    rows = rows_raw if isinstance(rows_raw, list) else []
    degraded_raw = payload.get("degraded_excluded")
    degraded_count = 0
    if isinstance(degraded_raw, dict):
        count = degraded_raw.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            degraded_count = count
    remedy = payload.get("remedy")
    return {
        "results": [row for row in rows if isinstance(row, dict)],
        "degraded": degraded_count > 0,
        "note": remedy if isinstance(remedy, str) else None,
    }

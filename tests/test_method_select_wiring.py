"""Select-method surface wiring: the config file must reach the handler.

Issue #325 family debt: ``analysis_select_method`` reads
``analysis.method_register_path`` from config, but an adapter that
drops the config silently answers from the packaged register while the
CLI and MCP honor the file. One temp config file drives all four legs
here; every leg must resolve the SAME register version. Key sets
cannot catch this drift (the parity gate compares keys, not values),
so these legs compare the value.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import cast

import asyncpg
import pytest
import yaml
from starlette.testclient import TestClient

from corpus_kb.cli._plumbing import flow
from corpus_kb.config import load_config

TASK = "Rank methods for labeling interview themes."
CUSTOM_VERSION = "9.9.9"
_BODY = {
    "task_description": TASK,
    "output_family": "labels",
    "inputs_available": ["embeddings", "labels"],
    "max_complexity_tier": "standard",
}


def _config_paths(tmp_path: Path) -> Path:
    """A register copy at ``CUSTOM_VERSION`` plus the config pointing at it."""
    ref = resources.files("corpus_kb") / "config" / "analysis_methods.yaml"
    with resources.as_file(ref) as packaged:
        raw = yaml.safe_load(packaged.read_text(encoding="utf-8"))
    raw["register_version"] = CUSTOM_VERSION
    register_path = tmp_path / "analysis_methods.yaml"
    register_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"analysis": {"method_register_path": str(register_path)}}),
        encoding="utf-8",
    )
    return config_path


async def test_http_adapter_passes_the_register_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _config_paths(tmp_path)
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(config_path))
    from corpus_kb.api.http import create_http_app
    from corpus_kb.api.research_http import set_research_pool

    set_research_pool(object())  # identity-only; the selector reads no data
    try:
        client = TestClient(create_http_app())
        response = client.post("/api/analysis/select-method", json=_BODY)
        assert response.status_code == 200, response.text
    finally:
        set_research_pool(None)
    envelope: dict[str, object] = response.json()
    assert envelope["register_version"] == CUSTOM_VERSION


async def test_socket_adapter_passes_the_register_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _config_paths(tmp_path)
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(config_path))
    from corpus_kb.api.research_http import set_research_pool
    from corpus_kb.api.socket import JSONRPCServer

    set_research_pool(object())  # identity-only; the selector reads no data
    try:
        server = JSONRPCServer()
        rpc = await server._process_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "analysis_select_method",
                    "params": _BODY,
                }
            )
        )
    finally:
        set_research_pool(None)
    assert "result" in rpc, rpc
    result = cast("dict[str, object]", rpc["result"])
    assert result["register_version"] == CUSTOM_VERSION


async def test_mcp_surface_passes_the_register_path(tmp_path: Path) -> None:
    """The MCP wrapper forwards the startup config it was built with."""
    config_path = _config_paths(tmp_path)
    from mcp.client._memory import InMemoryTransport
    from mcp.client.session import ClientSession

    from corpus_kb.mcp_server import build_mcp_server

    server = build_mcp_server(cast("asyncpg.Pool", object()), load_config(str(config_path)))
    transport = InMemoryTransport(server)
    async with (
        transport as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool("analysis_select_method", _BODY)
    assert not result.is_error, result.content[0].text
    envelope = json.loads(result.content[0].text)
    assert envelope["register_version"] == CUSTOM_VERSION


class _ClosingPool:
    """Identity-only pool stub; the flow helper closes it on every path."""

    async def close(self) -> None:
        return None


async def test_cli_flow_passes_the_register_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI flow helper hands its loaded config to the handler."""
    config_path = _config_paths(tmp_path)
    monkeypatch.setattr(
        "corpus_kb.cli.cli_ingest._load_config", lambda: load_config(str(config_path))
    )

    async def _open_pool(_cfg: dict[str, object]) -> asyncpg.Pool:
        return cast("asyncpg.Pool", _ClosingPool())  # reads no data

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open_pool)
    captured: list[dict[str, object]] = []

    def _emit(result: dict[str, object], json_output: bool) -> None:
        captured.append(result)

    exit_code = await flow(
        "method_select_tools",
        "analysis_select_method",
        {"task_description": TASK},
        True,
        _emit,
    )
    assert exit_code == 0
    assert captured[0]["register_version"] == CUSTOM_VERSION

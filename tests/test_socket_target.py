"""Issue #348 scope 1: one JSON-RPC endpoint per server.

``resolve_socket_target`` derives the endpoint from the server port (or
takes the explicit config key), and ``JSONRPCServer.start`` probes an
existing endpoint before touching it: a live server refuses with one
clear line, a stale socket file is reclaimed. The issue's Check — two
servers on ports 8020 and 8021 both answering — runs on each platform's
native derivation (unix paths on POSIX, port + 1000 on Windows).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.api.socket import JSONRPCServer
from corpus_kb.api.socket_target import resolve_socket_target
from corpus_kb.config import apply_env_overrides, get_default_config
from corpus_kb.errors import ConflictError

IS_WIN = sys.platform == "win32"


def _cfg(**server: Any) -> dict[str, Any]:
    return {"server": server}


def _bindable_unix_path(name: str) -> str:
    """A short unix socket path under /tmp.

    macOS caps unix socket paths at 104 bytes, and the pytest tmp dir on
    a macOS runner can exceed that cap ("AF_UNIX path too long"). /tmp
    stays short on every unix.
    """
    base = Path("/tmp") / f"corpus-kb-sock-tests-{os.getpid()}"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / name)


def _drop_unix_path(target: str) -> None:
    """Remove the socket file and the pid-scoped dir around it."""
    shutil.rmtree(Path(target).parent, ignore_errors=True)


class TestResolveSocketTarget:
    def test_default_port_keeps_default_path(self) -> None:
        assert resolve_socket_target(_cfg(port=8010), platform="linux") == "/tmp/corpus-kb.sock"

    def test_other_port_derives_suffixed_path(self) -> None:
        assert (
            resolve_socket_target(_cfg(port=8021), platform="linux") == "/tmp/corpus-kb-8021.sock"
        )

    def test_explicit_path_wins(self) -> None:
        target = resolve_socket_target(_cfg(port=8020, socket_path="/x/study.sock"), "linux")
        assert target == "/x/study.sock"

    def test_windows_default_port_is_8011(self) -> None:
        assert resolve_socket_target(_cfg(port=8010), platform="win32") == 8011

    def test_windows_other_port_is_plus_1000(self) -> None:
        assert resolve_socket_target(_cfg(port=8020), platform="win32") == 9020

    def test_windows_explicit_socket_port_wins(self) -> None:
        assert resolve_socket_target(_cfg(port=8020, socket_port=9500), "win32") == 9500

    def test_windows_ignores_socket_path(self) -> None:
        assert resolve_socket_target(_cfg(port=8020, socket_path="/x/y.sock"), "win32") == 9020

    def test_missing_server_block_still_derives(self) -> None:
        assert resolve_socket_target({}, platform="linux") == "/tmp/corpus-kb.sock"


def test_socket_path_env_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("CORPUS_KB_SOCKET_PATH", "/tmp/study-a.sock")
    config = get_default_config()
    apply_env_overrides(config)
    assert config["server"]["socket_path"] == "/tmp/study-a.sock"


def test_wire_http_uses_resolved_target(monkeypatch: Any) -> None:
    """Both engines derive the endpoint from the config (issue #348)."""
    import corpus_kb.api.http as http_mod
    import corpus_kb.api.socket as socket_mod
    import corpus_kb.server_wiring as wiring
    import corpus_kb.ui.spa as spa_mod

    seen: dict[str, Any] = {}

    def fake_get(target: str | int) -> Any:
        seen["target"] = target
        return object()

    monkeypatch.setattr(socket_mod, "get_socket_server", fake_get)
    monkeypatch.setattr(http_mod, "create_http_app", lambda: object())
    monkeypatch.setattr(spa_mod, "mount_spa", lambda app, cfg: False)
    monkeypatch.setattr(wiring, "wire_notebooks", lambda app, cfg: False)

    wiring._wire_http(_cfg(port=8021))
    expected = 9021 if IS_WIN else "/tmp/corpus-kb-8021.sock"
    assert seen["target"] == expected


async def _rpc_round_trip(target: str | int) -> dict[str, Any]:
    """One JSON-RPC round trip; an unknown method answers -32601."""
    if isinstance(target, int):
        reader, writer = await asyncio.open_connection("127.0.0.1", target)
    else:
        reader, writer = await asyncio.open_unix_connection(target)
    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "no-such-method"}
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 5)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    return dict(json.loads(line))


async def test_two_servers_on_8020_and_8021_both_answer() -> None:
    """The issue Check: two servers on ports 8020 and 8021 both answer."""
    targets = [resolve_socket_target(_cfg(port=port)) for port in (8020, 8021)]
    if IS_WIN:
        assert targets == [9020, 9021]
    else:
        assert targets == ["/tmp/corpus-kb-8020.sock", "/tmp/corpus-kb-8021.sock"]
    servers = [JSONRPCServer(target) for target in targets]
    try:
        await asyncio.gather(*(server.start() for server in servers))
        for target in targets:
            reply = await _rpc_round_trip(target)
            assert reply["error"]["code"] == -32601
    finally:
        for server in servers:
            await server.stop()
        if not IS_WIN:
            for target in targets:
                Path(str(target)).unlink(missing_ok=True)


async def test_second_server_refuses_a_live_endpoint() -> None:
    """A live socket is never taken over: one clear line, typed refusal."""
    target: str | int = 9030 if IS_WIN else _bindable_unix_path("study.sock")
    first = JSONRPCServer(target)
    second = JSONRPCServer(target)
    try:
        await first.start()
        with pytest.raises(ConflictError) as excinfo:
            await second.start()
        message = str(excinfo.value)
        assert "already listening" in message
        assert "\n" not in message  # one clear line
        reply = await _rpc_round_trip(target)  # the first server still answers
        assert reply["error"]["code"] == -32601
    finally:
        await first.stop()
        if not IS_WIN:
            _drop_unix_path(str(target))


@pytest.mark.skipif(IS_WIN, reason="stale-file recovery is a unix-socket mechanic")
async def test_stale_socket_file_is_reclaimed() -> None:
    """A socket file with no live server behind it does not block a start."""
    target = _bindable_unix_path("stale.sock")
    first = JSONRPCServer(target)
    await first.start()
    await first.stop()  # the file remains; nothing listens behind it
    second = JSONRPCServer(target)
    try:
        await second.start()  # must not raise
        reply = await _rpc_round_trip(target)
        assert reply["error"]["code"] == -32601
    finally:
        await second.stop()
        _drop_unix_path(target)


# ---------------------------------------------------------------------------
# The Windows TCP leg: the branch is platform-switched, not platform-bound —
# the derivation resolves a TCP port there, and asyncio serves it everywhere.
# Patching sys.platform exercises the branch honestly on every runner.
# ---------------------------------------------------------------------------


async def test_windows_tcp_branch_starts_and_refuses_a_live_port(monkeypatch: Any) -> None:
    """The Windows branch binds TCP; a live port is never taken over."""
    monkeypatch.setattr(sys, "platform", "win32")
    target = 9040
    first = JSONRPCServer(target)
    second = JSONRPCServer(target)
    try:
        await first.start()
        with pytest.raises(ConflictError) as excinfo:
            await second.start()
        assert "TCP port 9040" in str(excinfo.value)
        reply = await _rpc_round_trip(target)  # the first server still answers
        assert reply["error"]["code"] == -32601
    finally:
        await first.stop()


async def test_windows_legacy_string_target_keeps_port_8011(monkeypatch: Any) -> None:
    """A bare string target (legacy construction) still serves TCP 8011."""
    import corpus_kb.api.socket as socket_mod

    monkeypatch.setattr(sys, "platform", "win32")

    async def live(target: str | int) -> bool:
        return True

    monkeypatch.setattr(socket_mod, "_probe_live", live)
    server = JSONRPCServer(socket_mod.DEFAULT_SOCKET_PATH)
    with pytest.raises(ConflictError) as excinfo:
        await server.start()
    assert "TCP port 8011" in str(excinfo.value)


async def test_windows_bind_failure_maps_to_conflict(monkeypatch: Any) -> None:
    """A refused bind answers the typed conflict, never a traceback."""
    import corpus_kb.api.socket as socket_mod

    monkeypatch.setattr(sys, "platform", "win32")

    async def free(target: str | int) -> bool:
        return False

    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(socket_mod, "_probe_live", free)
    monkeypatch.setattr(socket_mod.asyncio, "start_server", refuse)
    server = JSONRPCServer(9041)
    with pytest.raises(ConflictError) as excinfo:
        await server.start()
    assert "Cannot listen on TCP port 9041" in str(excinfo.value)


def test_wiring_main_maps_typed_refusal_to_exit(monkeypatch: Any, capsys: Any) -> None:
    """main() answers a typed refusal with one stderr line and exit 3."""
    import corpus_kb.server_wiring as wiring

    async def refused(cfg: dict[str, Any] | None) -> dict[str, Any]:
        raise ConflictError("Another Corpus-KB server is already listening on the endpoint.")

    monkeypatch.setattr(wiring, "startup", refused)
    # The env port keeps _run from loading a config; the fake refuses first.
    monkeypatch.setenv("CORPUS_KB_PORT", "8010")
    with pytest.raises(SystemExit) as excinfo:
        wiring.main(["--transport", "http", "--port", "8010"])
    assert excinfo.value.code == 3
    assert "already listening" in capsys.readouterr().err

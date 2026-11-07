"""Behavior tests for the op runner (issue #351; 0.6.0 pinned).

Covers: unknown op, unknown arg, missing arg, JSON value parse, ``~``
expansion, route placeholders, GET query string, and the long-op timeout.
No test starts a real server: the HTTP seam is replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import op_client  # noqa: E402
import op_runner  # noqa: E402

SEARCH_ROW = {
    "name": "search",
    "family": "search",
    "params": (
        [{"name": "query", "required": True, "default": None}, {"name": "k", "default": 20}]
    ),
    "surfaces": [{"kind": "http", "route": "/api/search", "method": "POST"}],
}
EVENTS_ROW = {
    "name": "events_summary",
    "family": "events",
    "params": [{"name": "limit", "default": 20}],
    "surfaces": [{"kind": "http", "route": "/api/events/summary", "method": "GET"}],
}
DOC_ROW = {
    "name": "document_get",
    "family": "documents",
    "params": [{"name": "doc_id", "required": True, "default": None}],
    "surfaces": [{"kind": "http", "route": "/api/documents/{doc_id}", "method": "POST"}],
}
INVENTORY = {"operations": [SEARCH_ROW, EVENTS_ROW, DOC_ROW]}


def test_unknown_op_fails_with_inventory_hint() -> None:
    with pytest.raises(op_runner.OpError) as excinfo:
        op_runner.run_op("corpus-kb", "nonsense", [], "http://x", inventory=INVENTORY)
    assert "Unknown op 'nonsense'" in str(excinfo.value)


def test_unknown_arg_fails() -> None:
    with pytest.raises(op_runner.OpError) as excinfo:
        op_runner.run_op("corpus-kb", "search", ["query=x", "bogus=1"], "http://x",
                         inventory=INVENTORY)
    assert "bogus" in str(excinfo.value)


def test_missing_required_arg_fails() -> None:
    with pytest.raises(op_runner.OpError) as excinfo:
        op_runner.run_op("corpus-kb", "search", ["k=3"], "http://x", inventory=INVENTORY)
    assert "query" in str(excinfo.value)


def test_parse_value_json_when_it_parses() -> None:
    assert op_runner.parse_value("5") == 5
    assert op_runner.parse_value("true") is True
    assert op_runner.parse_value('{"a": 1}') == {"a": 1}
    assert op_runner.parse_value("hello world") == "hello world"
    assert op_runner.parse_value("~/notes") == "~/notes"  # not JSON: stays text


def test_expand_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows expanduser reads USERPROFILE; POSIX reads HOME. Set both.
    monkeypatch.setenv("HOME", str(Path.home()))
    monkeypatch.setenv("USERPROFILE", str(Path.home()))
    out = op_runner.expand_home({"file_path": "~/notes/a.txt", "k": 3})
    assert str(out["file_path"]).startswith(str(Path.home()))
    assert out["k"] == 3


def test_get_sends_args_as_query_string() -> None:
    method, route, payload = op_runner.build_request(EVENTS_ROW, {"limit": 5})
    assert method == "GET"
    assert route == "/api/events/summary?limit=5"
    assert payload is None


def test_post_sends_args_as_json_body() -> None:
    method, route, payload = op_runner.build_request(SEARCH_ROW, {"query": "x", "k": 3})
    assert method == "POST"
    assert route == "/api/search"
    assert payload == {"query": "x", "k": 3}


def test_route_placeholder_fills_and_consumes_arg() -> None:
    method, route, payload = op_runner.build_request(DOC_ROW, {"doc_id": "abc 123"})
    assert method == "POST"
    assert route == "/api/documents/abc%20123"
    assert payload == {}  # doc_id was consumed by the route


def test_route_placeholder_missing_fails() -> None:
    with pytest.raises(op_runner.OpError) as excinfo:
        op_runner.build_request(DOC_ROW, {})
    assert "doc_id" in str(excinfo.value)


def test_long_ops_get_7200_seconds() -> None:
    assert op_runner.timeout_for("ingest_file") == 7200
    assert op_runner.timeout_for("ingest_directory") == 7200
    assert op_runner.timeout_for("ingest_reembed") == 7200
    assert op_runner.timeout_for("ask") == op_runner.DEFAULT_TIMEOUT
    assert op_runner.timeout_for("ask") < 7200


def test_run_op_happy_path_post(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_request(url, method="GET", payload=None, timeout=120.0):
        seen.update(url=url, method=method, payload=payload, timeout=timeout)
        return {"status": "success"}

    monkeypatch.setattr(op_client, "request_json", fake_request)
    envelope = op_runner.run_op(
        "corpus-kb", "search", ["query=hello", "k=2"], "http://127.0.0.1:8010",
        inventory=INVENTORY,
    )
    assert envelope["status"] == "success"
    assert seen["url"] == "http://127.0.0.1:8010/api/search"
    assert seen["method"] == "POST"
    assert seen["payload"] == {"query": "hello", "k": 2}
    assert seen["timeout"] == op_runner.DEFAULT_TIMEOUT


def test_run_op_get_query_and_long_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_request(url, method="GET", payload=None, timeout=120.0):
        seen.update(url=url, method=method, timeout=timeout)
        return {"status": "success"}

    monkeypatch.setattr(op_client, "request_json", fake_request)
    op_runner.run_op("corpus-kb", "ingest_status", ["limit=7"], "http://127.0.0.1:8010/",
                     inventory={
                         "operations": [{
                             "name": "ingest_status",
                             "params": [{"name": "limit", "default": 20}],
                             "surfaces": [
                                 {"kind": "http", "route": "/api/ingest/status", "method": "GET"}
                             ],
                         }]
                     })
    assert seen["url"] == "http://127.0.0.1:8010/api/ingest/status?limit=7"
    assert seen["timeout"] == 7200  # the ingest family is long


def test_run_op_json_blob_merges_under_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_request(url, method="GET", payload=None, timeout=120.0):
        seen.update(payload=payload)
        return {"status": "success"}

    monkeypatch.setattr(op_client, "request_json", fake_request)
    op_runner.run_op(
        "corpus-kb", "search", ["k=2"], "http://x",
        json_blob='{"query": "from json"}', inventory=INVENTORY,
    )
    assert seen["payload"] == {"query": "from json", "k": 2}


def test_run_op_bad_json_blob_and_bad_pair() -> None:
    with pytest.raises(op_runner.OpError):
        op_runner.run_op("corpus-kb", "search", [], "http://x",
                         json_blob="{broken", inventory=INVENTORY)
    with pytest.raises(op_runner.OpError):
        op_runner.run_op("corpus-kb", "search", ["noequals"], "http://x", inventory=INVENTORY)


def test_run_op_tilde_expands_path_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_request(url, method="GET", payload=None, timeout=120.0):
        seen.update(payload=payload)
        return {"status": "success"}

    monkeypatch.setattr(op_client, "request_json", fake_request)
    monkeypatch.setenv("HOME", str(Path.home()))
    monkeypatch.setenv("USERPROFILE", str(Path.home()))
    row = {
        "name": "ingest_file",
        "params": [{"name": "file_path", "required": True}],
        "surfaces": [{"kind": "http", "route": "/api/ingest/file", "method": "POST"}],
    }
    op_runner.run_op("corpus-kb", "ingest_file", ["file_path=~/notes.txt"], "http://x",
                     inventory={"operations": [row]})
    assert Path(seen["payload"]["file_path"]) == Path("~/notes.txt").expanduser()


def test_http_error_surfaces_body_excerpt() -> None:
    with pytest.raises(op_client.HttpError) as excinfo:
        op_client.request_json("http://127.0.0.1:1/nothing", timeout=2)
    assert "Cannot reach" in str(excinfo.value)

"""The op runner: any registry op against a study server (issue #351).

Checks the op name, unknown args, and missing args against the live
inventory (``corpus-kb operations``), then sends one HTTP call. Values
parse as JSON when they parse. Path values that start with ``~`` expand.
``{placeholder}`` route segments fill from args. GET ops send args as a
query string. Long ops (the ingest family) get a 7200 second timeout.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from pathlib import Path

import op_client

DEFAULT_TIMEOUT = 600.0
LONG_TIMEOUT = 7200.0
INVENTORY_TIMEOUT = 120


class OpError(Exception):
    """A user-facing op validation failure (name, args, or route)."""


def load_inventory(cli: str) -> dict:
    """The live operation inventory from ``corpus-kb operations``."""
    result = subprocess.run(
        [cli, "operations", "--indent", "0"],
        capture_output=True,
        text=True,
        timeout=INVENTORY_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:400]
        raise OpError(f"corpus-kb operations failed: {detail}")
    try:
        return dict(json.loads(result.stdout))
    except ValueError as exc:
        raise OpError("corpus-kb operations printed a non-JSON document.") from exc


def find_op(inventory: dict, name: str) -> dict | None:
    for row in inventory.get("operations", []):
        if row.get("name") == name:
            return dict(row)
    return None


def http_surface(op_row: dict) -> dict | None:
    for surface in op_row.get("surfaces", []):
        if surface.get("kind") == "http":
            return dict(surface)
    return None


def parse_value(raw: str) -> object:
    """Values parse as JSON when they parse; everything else stays text."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def expand_home(args: dict) -> dict:
    """Expand a leading ``~`` in string values to the user's home folder."""
    out: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("~"):
            value = str(Path(value).expanduser())
        out[key] = value
    return out


def validate(op_row: dict, name: str, args: dict) -> None:
    """Unknown and missing args fail against the inventory row."""
    params = {str(p.get("name")): p for p in op_row.get("params", [])}
    unknown = sorted(set(args) - set(params))
    if unknown:
        known = ", ".join(sorted(params)) or "(none)"
        raise OpError(f"Unknown argument(s) for op '{name}': {', '.join(unknown)}. Known: {known}.")
    missing = [
        pname for pname, param in params.items() if param.get("required") and pname not in args
    ]
    if missing:
        raise OpError(f"Missing required argument(s) for op '{name}': {', '.join(missing)}.")


def build_request(op_row: dict, args: dict) -> tuple[str, str, dict | None]:
    """Turn the HTTP surface and args into method, path, and payload.

    ``{placeholder}`` route segments consume their arg. GET sends the rest
    as a query string; POST sends it as the JSON body.
    """
    surface = http_surface(op_row)
    if surface is None:
        raise OpError(f"Op '{op_row.get('name')}' has no HTTP surface.")
    method = str(surface.get("method", "GET")).upper()
    route = str(surface.get("route", ""))
    remaining: dict[str, object] = dict(args)
    for placeholder in re.findall(r"\{(\w+)\}", route):
        if placeholder not in remaining:
            raise OpError(f"Route {route} needs a value for '{placeholder}'.")
        value = remaining.pop(placeholder)
        route = route.replace("{" + placeholder + "}", urllib.parse.quote(str(value), safe=""))
    if method == "GET":
        pairs = {k: v for k, v in remaining.items() if v is not None}
        query = urllib.parse.urlencode(pairs, doseq=True)
        return "GET", route + (f"?{query}" if query else ""), None
    return "POST", route, remaining


def timeout_for(name: str) -> float:
    """Ingest-family ops are long: 7200 s. Everything else: 600 s."""
    return LONG_TIMEOUT if name.startswith("ingest") else DEFAULT_TIMEOUT


def run_op(
    cli: str,
    name: str,
    pairs: list[str],
    base_url: str,
    json_blob: str | None = None,
    timeout: float | None = None,
    inventory: dict | None = None,
) -> dict:
    """Validate one op call against the inventory, then send it."""
    rows = inventory if inventory is not None else load_inventory(cli)
    op_row = find_op(rows, name)
    if op_row is None:
        raise OpError(f"Unknown op '{name}'. Run `corpus-rag ops` for the live inventory.")
    args: dict[str, object] = {}
    if json_blob:
        try:
            parsed = json.loads(json_blob)
        except ValueError as exc:
            raise OpError("--json must hold valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise OpError("--json must hold a JSON object.")
        args.update(parsed)
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise OpError(f"Arguments look like key=value. Got '{pair}'.")
        args[key.strip()] = parse_value(raw)
    validate(op_row, name, args)
    args = expand_home(args)
    method, route, payload = build_request(op_row, args)
    effective = timeout if timeout is not None else timeout_for(name)
    base_host = urllib.parse.urlsplit(base_url).hostname
    allowed = frozenset({base_host}) if base_host else None
    return op_client.request_json(
        base_url.rstrip("/") + route,
        method=method,
        payload=payload,
        timeout=effective,
        allowed_hosts=allowed,
    )

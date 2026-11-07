"""Shared seams for the codify commands: errors, op send, SQL, file reads.

Every codify command turns the study server up through
``study_commands.scoped_study`` (study selection plus auto drain) and
sends one op through the op runner. ``codify cells`` and the
``pool`` default version read through the read-only SQL route instead.

Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import op_client
import op_runner
import study_server


class CodifyError(Exception):
    """A user-facing codify failure; the dispatcher prints it with its code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def send(
    cli: str, name: str, pairs: list[str], study: dict, json_blob: str | None = None
) -> dict:
    """One op call; failures come back as ``CodifyError`` with an exit code."""
    try:
        return op_runner.run_op(
            cli, name, pairs, study_server.base_url(study), json_blob=json_blob
        )
    except op_runner.OpError as exc:
        raise CodifyError(2, str(exc)) from exc
    except op_client.HttpError as exc:
        raise CodifyError(1, str(exc)) from exc


def sql(study: dict, statement: str) -> list[dict]:
    """One read-only SQL call through the study server's query route."""
    url = study_server.base_url(study) + "/api/query/sql"
    try:
        envelope = op_client.post_json(url, {"sql": statement}, timeout=120)
    except op_client.HttpError as exc:
        raise CodifyError(1, str(exc)) from exc
    rows = envelope.get("result")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def read_json_text(path: str) -> str:
    """The file text; a typed error when it cannot be read."""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise CodifyError(1, f"Cannot read {path}: {exc}") from exc


def parse_json(text: str, path: str) -> object:
    """Parsed JSON; a typed error when the text is not JSON."""
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CodifyError(1, f"{path} is not valid JSON: {exc}") from exc


def parse_json_list(path: str, cells_key: str = "cells") -> list:
    """A JSON list, or an object holding one under ``cells_key``."""
    data = parse_json(read_json_text(path), path)
    if isinstance(data, dict) and cells_key in data:
        data = data[cells_key]
    if not isinstance(data, list):
        raise CodifyError(
            1, f"{path} must hold a JSON list, or an object with a '{cells_key}' key."
        )
    return data

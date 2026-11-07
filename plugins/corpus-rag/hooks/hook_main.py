"""Shared main() for the corpus-rag hook bodies (issue #352).

Reads one JSON object from stdin, runs the event function, prints what
it returns, and fails open: any error records into hook-errors.jsonl and
exits 0, so a broken hook never blocks a session.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def read_json(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def run_event(event: str, runner) -> int:
    import hook_state

    try:
        out = runner(read_json(sys.stdin.read()))
    except Exception as exc:  # fail open: record, never block
        hook_state.record_error(event, repr(exc))
        return 0
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    return 0

"""Rollup, researcher lines, and contract parity for UXR (issue #355).

The rollup counts stances per question from verified rows, then adds
researcher lines from ``interviews/*-summary.md``. A researcher line
wins over the model rows for its question. ``contract-test`` compares
the rollup JSON with the report tool's ``evidence-weight`` output and
fails on any difference.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import uxr_files

KIND_ORDER = {"rq": 0, "h": 1, "a": 2}
MAX_UNVERIFIED_DEFAULT = 0.1

_RESEARCHER = re.compile(r"^(RQ\d+|H\d+|A\d+)\s*[:\-—]\s*(.+)$", re.IGNORECASE)
_UNIT_KEY = re.compile(r"^(RQ|H|A)(\d+)$", re.IGNORECASE)


class ContractError(Exception):
    """The report tool failed, or its output is not JSON."""


def _unit_sort_key(unit_id: str) -> tuple[int, int]:
    match = _UNIT_KEY.match(unit_id)
    if match is None:
        return (9, 0)
    kind = {"RQ": "rq", "H": "h", "A": "a"}[match.group(1).upper()]
    return (KIND_ORDER[kind], int(match.group(2)))


def counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Stance counts per question, verified rows only, stable order."""
    out: dict[str, dict[str, int]] = {}
    for row in sorted(rows, key=lambda r: _unit_sort_key(str(r.get("unit_id") or ""))):
        if not row.get("verified"):
            continue
        unit = str(row.get("unit_id") or "?")
        stance = str(row.get("stance") or "?")
        out.setdefault(unit, {})
        out[unit][stance] = out[unit].get(stance, 0) + 1
    return out


def researcher_lines(text: str) -> dict[str, str]:
    """Question-id lines outside the marked block of one summary file."""
    lines: dict[str, str] = {}
    inside_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == uxr_files.BEGIN:
            inside_block = True
            continue
        if stripped == uxr_files.END:
            inside_block = False
            continue
        if inside_block:
            continue
        match = _RESEARCHER.match(stripped)
        if match:
            lines.setdefault(match.group(1).upper(), match.group(2).strip())
    return lines


def researcher_from_dir(root) -> dict[str, str]:
    """Researcher lines merged over ``interviews/*-summary.md``."""
    merged: dict[str, str] = {}
    folder = Path(root) / "interviews"
    if not folder.is_dir():
        return merged
    for path in sorted(folder.glob("*-summary.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for qid, line in researcher_lines(text).items():
            merged.setdefault(qid, line)
    return merged


def build(rows: list[dict], researcher: dict[str, str]) -> dict:
    """The rollup: model counts, researcher lines, and unverified totals."""
    verified = sum(1 for row in rows if row.get("verified"))
    total = len(rows)
    return {
        "units": counts(rows),
        "researcher": dict(researcher),
        "totals": {
            "verified": verified,
            "unverified": total - verified,
            "share": ((total - verified) / total) if total else 0.0,
        },
    }


def over_limit(report: dict, max_unverified: float) -> bool:
    """True when the unverified share is over the limit."""
    return report["totals"]["share"] > max_unverified


def rollup_json(report: dict) -> dict:
    """The canonical JSON for the contract test."""
    return {
        "units": report["units"],
        "researcher": report["researcher"],
        "totals": report["totals"],
    }


def contract_diff(ours: dict, theirs: dict) -> list[str]:
    """One line per difference; empty means parity."""
    diffs: list[str] = []
    for unit, stances in ours.get("units", {}).items():
        other = theirs.get("units", {}).get(unit)
        if other is None:
            diffs.append(f"unit {unit} missing from the evidence")
            continue
        for stance, count in stances.items():
            if other.get(stance) != count:
                diffs.append(
                    f"unit {unit} stance {stance}: ours {count}, evidence {other.get(stance)}"
                )
    if ours.get("researcher") != theirs.get("researcher"):
        diffs.append("researcher lines differ")
    if ours.get("totals") != theirs.get("totals"):
        diffs.append("totals differ")
    return diffs


def evidence_from_tool(command: str, payload: dict) -> dict:
    """Run the report tool; its stdin gets the rollup, stdout is JSON.

    The command line comes from the ``uxr_report_tool`` userConfig option
    (exported as ``CLAUDE_PLUGIN_OPTION_UXR_REPORT_TOOL``).
    """
    parts = [part.strip('"') for part in shlex.split(command, posix=(os.name != "nt"))]
    parts = [part for part in parts if part]
    if not parts:
        raise ContractError("the report tool command is empty")
    try:
        done = subprocess.run(
            parts,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"the report tool failed to run: {exc}") from exc
    if done.returncode != 0:
        raise ContractError(
            f"the report tool exited {done.returncode}: {done.stderr.strip()[:200]}"
        )
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise ContractError("the report tool did not print JSON") from exc

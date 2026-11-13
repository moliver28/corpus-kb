"""Stance rows for the UXR layer: enums, unit ids, validation (#355).

``rq`` stances are free text, or exactly ``No direct evidence this
session.``; ``h`` is Supports, Challenges, Mixed, No direct evidence;
``a`` is Holds, Breaks, Untested. A row that fails any check stores as
``unverified``. A near-miss quote passes but is logged.

Stdlib only.
"""

from __future__ import annotations

import re

import uxr_quotes
import uxr_store

STANCES_H = ("Supports", "Challenges", "Mixed", "No direct evidence")
STANCES_A = ("Holds", "Breaks", "Untested")
NO_EVIDENCE_RQ = "No direct evidence this session."

_UNIT = re.compile(r"^(RQ|H|A)(\d+)$")


def unit_kind(unit_id: str) -> str | None:
    """``rq``, ``h``, or ``a`` from the id prefix; None when unknown."""
    match = _UNIT.match(unit_id or "")
    if match is None:
        return None
    prefix = match.group(1)
    return {"RQ": "rq", "H": "h", "A": "a"}[prefix]


def stance_ok(kind: str | None, stance: str) -> bool:
    """The stance rule for one unit kind."""
    if kind == "rq":
        return bool(stance and stance.strip())
    if kind == "h":
        return stance in STANCES_H
    if kind == "a":
        return stance in STANCES_A
    return False


def unit_number(unit_id: str) -> int:
    match = _UNIT.match(unit_id or "")
    return int(match.group(2)) if match else 0


def validate_rows(
    conn, rows: list[dict], store: bool = False, checker=uxr_quotes.check_quote
) -> dict:
    """Validate stance rows against the stored questions.

    Returns a report: one entry per row with ``ok``, ``verified``,
    ``quote_class``, and ``problems``; plus the ``near_misses`` log and
    the stored totals. With ``store`` the rows land in the store, failed
    ones as ``unverified``.
    """
    known = {q["id"]: q["kind"] for q in uxr_store.questions(conn)}
    report: dict = {"rows": [], "stored": 0, "unverified": 0, "near_misses": []}
    to_save: list[dict] = []
    for row in rows:
        entry = _validate_one(row, known, checker)
        report["rows"].append(entry)
        if entry["quote_class"] == uxr_quotes.NEAR_MISS:
            report["near_misses"].append(
                f"{row.get('session')}/{row.get('unit_id')}: {row.get('quote')}"
            )
        if entry["verified"]:
            report["stored"] += 1
        else:
            report["unverified"] += 1
        to_save.append(
            {
                "session": row.get("session"),
                "unit_id": row.get("unit_id"),
                "stance": row.get("stance"),
                "quote": row.get("quote"),
                "quote_class": entry["quote_class"],
                "rationale": row.get("rationale"),
                "verified": entry["verified"],
            }
        )
    if store:
        study = uxr_store.get_study(conn)
        if study is not None:
            uxr_store.save_rows(conn, study["id"], to_save)
    return report


def _validate_one(row: dict, known: dict[str, str], checker) -> dict:
    unit_id = str(row.get("unit_id") or "")
    stance = str(row.get("stance") or "")
    quote = str(row.get("quote") or "")
    unit_text = str(row.get("unit_text") or "")
    session = str(row.get("session") or "")
    problems: list[str] = []
    kind = known.get(unit_id)
    if unit_kind(unit_id) is None or kind is None:
        problems.append(f"unknown unit id {unit_id!r}")
    elif not stance_ok(kind, stance):
        problems.append(f"bad stance {stance!r} for a {kind} unit")
    quote_class: str | None = None
    if quote:
        if not unit_text:
            problems.append("no unit text to check the quote against")
        else:
            quote_class = checker(quote, unit_text)
            if quote_class == uxr_quotes.FAIL:
                problems.append("quote is not in the unit text")
    elif kind is not None and stance != NO_EVIDENCE_RQ:
        problems.append("no quote to check")
    if not session:
        problems.append("no session on the row")
    return {
        "session": session,
        "unit_id": unit_id,
        "stance": stance,
        "ok": not problems,
        "verified": not problems,
        "quote_class": quote_class,
        "problems": problems,
    }

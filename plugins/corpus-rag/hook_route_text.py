"""Route-derived nudge text for the analyze lane (plan Task 4).

The analyze hook shells out to ``corpus-kb analysis-route`` (mark-gated
first, 10 s timeout, fail-open). This module turns the route envelope
into the nudge text: the top method with its why on line one, the run
command from ``run_hint`` on line two. A degraded route says so. Any
malformed route yields an empty string, and the caller keeps the static
lane text. The engine supplies data; the plugin owns the words.

Envelope keys (verbatim from ``analysis_route_envelope.py``):
``status``, ``ranked[{method_id, name, why, run_hint{op, params}}]``.

Stdlib only.
"""

from __future__ import annotations

WHY_WORD_CAP = 20
DEGRADED_PREFIX = "Routing ran in degraded mode."


def _clip(text: str, cap: int) -> str:
    words = text.split()
    return " ".join(words[:cap])


def route_nudge_text(route: object, question: str) -> str:
    """The two-line nudge from one route envelope, or "" when malformed."""
    if not isinstance(route, dict):
        return ""
    ranked = route.get("ranked")
    if not isinstance(ranked, list) or not ranked:
        return ""
    top = ranked[0]
    if not isinstance(top, dict):
        return ""
    name = str(top.get("name") or "").strip()
    why = str(top.get("why") or "").strip()
    hint = top.get("run_hint")
    if not name or not isinstance(hint, dict):
        return ""
    op = str(hint.get("op") or "").strip()
    if not op:
        return ""
    first = _clip(f"Top method: {name} — {why}", WHY_WORD_CAP)
    command = f"corpus-rag op {op}{_params_text(hint.get('params'))}"
    lines = [first, f"Run it with: {command}"]
    if route.get("status") == "degraded":
        lines.insert(0, DEGRADED_PREFIX)
    return "\n".join(lines)


def _params_text(params: object) -> str:
    """The run_hint params as key=value pairs; empty when absent."""
    if not isinstance(params, dict):
        return ""
    pairs = [f"{key}={value}" for key, value in params.items()]
    return (" " + " ".join(pairs)) if pairs else ""

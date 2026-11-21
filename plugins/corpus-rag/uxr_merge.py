"""Coded-evidence merge and advisory second opinions (plan Task 5).

The engine's ``coding_export`` rows join accepted codes with chunk text
and document source. ``merge_coded_evidence`` appends them to the themes
text as one marked section; a re-run replaces its own section and never
touches the model narrative. ``quote_second_opinion`` asks the engine's
search op for nearby corpus text; it is advisory only, and the difflib
quote check stays the contract gate. The ``search`` op takes a plain
query, so a quote or a file stem can drive it (``search_similar`` needs
a stored chunk id, which these call sites do not have).

Stdlib only.
"""

from __future__ import annotations

import re

EVIDENCE_BEGIN = "corpus:evidence-begin"
EVIDENCE_END = "corpus:evidence-end"
SECTION_HEADER = "Coded evidence (engine coding_export, not model output):"

_STRIP = re.compile(r"(?ms)^" + EVIDENCE_BEGIN + r"$.*?^" + EVIDENCE_END + r"$\n?")


def merge_coded_evidence(themes_text: str, rows: list[dict]) -> str:
    """The themes text with one coded-evidence section; idempotent.

    A previous section from an earlier run is replaced in place. The
    model narrative before and after the section never changes.
    """
    text = _STRIP.sub("", themes_text)
    section = render_coded_evidence(rows)
    if not section:
        return text
    base = text.rstrip("\n")
    return f"{base}\n\n{EVIDENCE_BEGIN}\n{section}\n{EVIDENCE_END}\n"


def render_coded_evidence(rows: list[dict]) -> str:
    """One line per coded row, sorted for a stable snapshot."""
    clean = [row for row in rows if isinstance(row, dict)]
    if not clean:
        return ""
    lines = [SECTION_HEADER, ""]
    ordered = sorted(
        clean,
        key=lambda r: (
            str(r.get("code_id") or ""),
            str(r.get("chunk_id") or ""),
            str(r.get("text") or ""),
        ),
    )
    for row in ordered:
        code = str(row.get("code_id") or "?")
        source = str(row.get("document_source") or "?")
        text = str(row.get("text") or "").strip().replace("\n", " ")
        confidence = row.get("confidence")
        conf = f" (confidence {float(confidence):.2f})" if isinstance(confidence, (int, float)) else ""
        lines.append(f"- [{code}] {text} — {source}{conf}")
    return "\n".join(lines)


def quote_second_opinion(quote: str, sender) -> dict | None:
    """The advisory search envelope for one quote, or None on any failure.

    ``sender(op_name, pairs)`` runs one op through the caller's study
    connection. The result carries ``advisory: True`` so no caller can
    mistake it for the contract gate; the difflib check stays the gate.
    """
    if not quote or not quote.strip():
        return None
    try:
        envelope = sender("search", [f"query={quote.strip()}", "k=3"])
    except Exception:
        return None
    if not isinstance(envelope, dict):
        return None
    return {**envelope, "advisory": True}


def coded_rows_from_export(envelope: object) -> list[dict]:
    """The coding_export rows from one op envelope; empty unless valid."""
    if not isinstance(envelope, dict) or envelope.get("status") != "success":
        return []
    rows = envelope.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# Study-backed helpers (fail-open; call sites stay quiet when down)
# ---------------------------------------------------------------------------


def coded_rows(args_study_slug: str | None) -> list[dict]:
    """The coding_export rows for one study; empty on any failure."""
    try:
        import codify_common
        import study_commands

        cli, _slug, study = study_commands.scoped_study(args_study_slug)
        envelope = codify_common.send(cli, "coding_export", [], study)
        return coded_rows_from_export(envelope)
    except Exception:
        return []


def nearest_documents(args_study_slug: str | None, query: str, k: int = 3) -> dict | None:
    """The advisory search envelope for one query; None on any failure.

    The ``search`` op takes a plain query, so file stems and quotes
    work. ``search_similar`` needs a stored chunk id, which these call
    sites do not have.
    """
    if not query or not query.strip():
        return None
    try:
        import op_runner
        import study_commands
        import study_server

        cli, _slug, study = study_commands.scoped_study(args_study_slug)
        return op_runner.run_op(
            cli, "search", [f"query={query.strip()}", f"k={k}"],
            study_server.base_url(study),
        )
    except Exception:
        return None


def search_sender(args_study_slug: str | None):
    """A ``quote_second_opinion`` sender bound to one study."""
    def sender(op_name: str, pairs: list[str]) -> dict:
        import op_runner
        import study_commands
        import study_server

        cli, _slug, study = study_commands.scoped_study(args_study_slug)
        return op_runner.run_op(cli, op_name, pairs, study_server.base_url(study))

    return sender

"""Spec rows for the #202 bulk re-embed socket arm (pure data).

The split module keeps ``_socket_rows.py`` under the 250-line rule (the
same move the issue #350 coding/event rows made). ``_socket_rows`` merges
this dict into its table at import.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

_REINGEST = "corpus_kb.tools.ingest_reingest"

REINGEST_SPECS: dict[str, _Spec] = {
    "ingest_reingest": _Spec(
        _REINGEST,
        "ingest_reingest",
        opts=(
            _Opt("project_id", "str"),
            _Opt("dry_run", "bool", False),
            _Opt("batch_size", "int", 128),
        ),
    ),
}

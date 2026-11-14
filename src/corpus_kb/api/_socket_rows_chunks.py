"""Spec rows for the chunk-read socket arm (pure data; Phase-2 Wave 5).

``chunks_list`` is the drawer NLP panel's chunk window read; its arm
rides this split of ``_socket_rows`` for the 250-line rule.
``_socket_rows`` merges the rows into ``_SPECS`` at import; this module
holds no behavior.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

_CHUNK = "corpus_kb.tools.chunk_read_tools"

CHUNK_SPECS: dict[str, _Spec] = {
    "chunks_list": _Spec(
        _CHUNK,
        "chunks_list",
        positional=(_Opt("doc_id", "req"),),
        # Counts ride raw on purpose: the shared window-count guard
        # (tools._count_input) owns the canonical rule, so the socket arm
        # answers the same typed message the other surfaces answer.
        opts=(_Opt("limit", "raw"), _Opt("offset", "raw")),
    ),
}

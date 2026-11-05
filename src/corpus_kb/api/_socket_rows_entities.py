"""Spec rows for the entity-read socket arm (pure data; Phase-2 Wave 4).

``list_entities`` lived handler-only until Wave 4; its arm rides this
split of ``_socket_rows`` for the 250-line rule. ``_socket_rows`` merges
the rows into ``_SPECS`` at import; this module holds no behavior.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

_ENTITY = "corpus_kb.tools.entity_read_tools"

ENTITY_SPECS: dict[str, _Spec] = {
    "list_entities": _Spec(
        _ENTITY,
        "list_entities",
        opts=(
            _Opt("entity_type", "str"),
            _Opt("limit", "int", 100),
            _Opt("offset", "int", 0),
        ),
    ),
}

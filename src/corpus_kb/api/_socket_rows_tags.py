"""Spec rows for the tag/metadata socket arms (pure data; Phase-2 Wave 2).

The reads (issue #182) and the writes (the drawer's tag and metadata
edits) live in this split of ``_socket_rows`` for the 250-line rule.
``_socket_rows`` merges the rows into ``_SPECS`` at import; this module
holds no behavior.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

_TAG = "corpus_kb.tools.tag_tools"

TAG_SPECS: dict[str, _Spec] = {
    "get_document_tags": _Spec(
        _TAG,
        "get_document_tags",
        positional=(_Opt("doc_id", "req"),),
    ),
    "get_metadata": _Spec(_TAG, "get_metadata", opts=(_Opt("key", "str"), _Opt("doc_id", "raw"))),
    "add_tag": _Spec(
        _TAG,
        "add_tag",
        positional=(_Opt("name", "req"),),
        opts=(_Opt("color", "str"), _Opt("description", "str")),
    ),
    "tag_document": _Spec(
        _TAG, "tag_document", positional=(_Opt("doc_id", "req"), _Opt("tag", "req"))
    ),
    "untag_document": _Spec(
        _TAG, "untag_document", positional=(_Opt("doc_id", "req"), _Opt("tag", "req"))
    ),
    "set_metadata": _Spec(
        _TAG,
        "set_metadata",
        positional=(_Opt("key", "req"), _Opt("value", "req")),
        opts=(_Opt("doc_id", "raw"),),
    ),
}

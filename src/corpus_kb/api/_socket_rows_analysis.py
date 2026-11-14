"""Spec rows for the analysis-family socket arms (pure data).

Split out of ``_socket_rows.py`` for the 250-line rule: the statements,
conflicts, beliefs, corpus-stats, queue, and per-project rows move here
verbatim; ``_socket_rows`` merges the two dicts at import.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

_STMT = "corpus_kb.tools.statements_tools"
_CONF = "corpus_kb.tools.conflicts_tools"
_BELIEFS = "corpus_kb.tools.beliefs_tools"
_QUEUE = "corpus_kb.tools.queue_tools"
_PROJECT = "corpus_kb.tools.project_tools"
_VERSIONING = "corpus_kb.tools.versioning_tools"

STATEMENTS_SPECS: dict[str, _Spec] = {
    "statements_list": _Spec(
        _STMT,
        "statements_list",
        opts=(_Opt("template_id", "str"), _Opt("document_id", "str"), _Opt("limit", "int", 50)),
    ),
    "statements_timeline": _Spec(
        _STMT, "statements_timeline", opts=(_Opt("text_query", "str"), _Opt("limit", "int", 200))
    ),
    "conflicts_scan": _Spec(_CONF, "conflicts_scan"),
    "conflicts_list": _Spec(
        _CONF, "conflicts_list", opts=(_Opt("verdict", "str"), _Opt("limit", "int", 50))
    ),
    "conflicts_resolve": _Spec(
        _CONF,
        "conflicts_resolve",
        opts=(
            _Opt("conflict_id", "sdef", ""),
            _Opt("resolved_verdict", "sdef", ""),
            _Opt("resolved_by", "sdef", ""),
        ),
    ),
    "beliefs_current": _Spec(
        _BELIEFS,
        "beliefs_current",
        opts=(_Opt("subject", "str"), _Opt("limit", "int", 50), _Opt("rebuild", "bool", True)),
    ),
    "beliefs_timeline": _Spec(
        _BELIEFS,
        "beliefs_timeline",
        opts=(_Opt("subject", "str"), _Opt("relation", "str"), _Opt("object", "str")),
    ),
    "beliefs_predict": _Spec(
        _BELIEFS,
        "beliefs_predict",
        opts=(_Opt("subject", "str"), _Opt("relation", "str"), _Opt("object", "str")),
    ),
    "beliefs_compare": _Spec(_BELIEFS, "beliefs_compare", opts=(_Opt("subject", "str"),)),
    # Phase-2 Wave 3: the corpus stats pair (tools/versioning_tools.py).
    "stats": _Spec(_VERSIONING, "stats"),
    "document_stats": _Spec(_VERSIONING, "document_stats"),
    "queue_enqueue": _Spec(
        _QUEUE,
        "queue_enqueue",
        opts=(
            _Opt("path", "sdef", ""),
            _Opt("title", "str"),
            _Opt("profile", "str"),
            _Opt("strategy_config", "raw"),
            _Opt("project_id", "str"),
        ),
    ),
    "queue_status": _Spec(
        _QUEUE, "queue_status", opts=(_Opt("queue_id", "str"), _Opt("limit", "int", 50))
    ),
    "queue_cancel": _Spec(_QUEUE, "queue_cancel", opts=(_Opt("queue_id", "sdef", ""),)),
    # -- Track C: the per-project raw-table ops --
    "project_provision": _Spec(
        _PROJECT,
        "project_provision",
        opts=(_Opt("slug", "sdef", ""), _Opt("project_id", "str")),
    ),
    "project_list": _Spec(_PROJECT, "project_list"),
    "project_drop": _Spec(_PROJECT, "project_drop", opts=(_Opt("slug", "sdef", ""),)),
}

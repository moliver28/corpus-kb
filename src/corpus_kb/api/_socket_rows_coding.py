"""Spec rows for the coding + events socket arms (issue #350, pure data).

One row per chunk-coding and event-store health operation: the lazy
import target, the pool slot, and the strict coercion per parameter.
``socket_ops`` turns these rows into arms once at import; this module
holds no behavior (the split keeps ``_socket_rows.py`` under the
250-line rule, same as the tags/entities splits).
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

# Lazy-import targets (one short name per tool module).
_CODING = "corpus_kb.tools.coding_tools"
_EVENTS = "corpus_kb.tools.events_tools"

CODING_SPECS: dict[str, _Spec] = {
    # The JSON-object/list params (codebook, cells) ride as raw opts: the
    # positional kinds stringify, and these must reach the tool untouched.
    "coding_embed_codebook": _Spec(
        _CODING,
        "coding_embed_codebook",
        opts=(_Opt("codebook", "raw"),),
    ),
    "coding_pool": _Spec(
        _CODING,
        "coding_pool",
        positional=(_Opt("codebook_version_id", "req"),),
    ),
    "coding_code_batch": _Spec(
        _CODING,
        "coding_code_batch",
        opts=(
            _Opt("cells", "raw"),
            _Opt("coder", "str"),
            _Opt("model", "str"),
            _Opt("batch_id", "str"),
            _Opt("coding_pass", "int", 1),
            _Opt("bands", "raw"),
        ),
    ),
    "coding_reliability": _Spec(
        _CODING,
        "coding_reliability",
        opts=(
            _Opt("per_code_min", "int", 5),
            _Opt("seed", "int", 17),
            _Opt("code_universe", "raw"),
        ),
    ),
    "coding_calibrate_floors": _Spec(
        _CODING,
        "coding_calibrate_floors",
        positional=(
            _Opt("code_id", "req"),
            _Opt("codebook_version_id", "req"),
        ),
        opts=(
            _Opt("positive_chunk_ids", "raw"),
            _Opt("negative_chunk_ids", "raw"),
            _Opt("min_pos", "int", 10),
            _Opt("global_fallback", "float"),
        ),
    ),
    "coding_saturation": _Spec(
        _CODING,
        "coding_saturation",
        opts=(
            _Opt("batch_id", "str"),
            _Opt("threshold", "float", 0.05),
            _Opt("min_samples", "int", 50),
        ),
    ),
    "coding_status": _Spec(_CODING, "coding_status"),
    "coding_export": _Spec(
        _CODING,
        "coding_export",
        opts=(_Opt("limit", "int", 1000), _Opt("offset", "int", 0)),
    ),
}

EVENTS_SPECS: dict[str, _Spec] = {
    "events_summary": _Spec(_EVENTS, "events_summary"),
}

"""Socket method table: one arm per catalogue operation (full parity).

The full-surface parity wave replaced the ``elif`` chain in
``api/socket.py`` with this table: one arm per operation, every catalogue
socket surface dispatchable. Three arm shapes:

  * Spec arms: data rows in ``_socket_rows._SPECS`` — one lazy tool
    import, one pool fetch, and strict parameter coercion (issue #238).
  * Hand arms: ``_socket_arms`` functions for bespoke work — domain
    command construction, envelope assembly, imported constant defaults.
  * Route arms: generated from ``research_http.tool_routes()`` — the SAME
    table the HTTP surface registers. One table, two transports, so a new
    research/release route is socket-reachable the moment it exists.

The dispatch table is built ONCE at import (``_ARM_TABLE``); dispatch
never rebuilds it. Handler imports stay lazy — each arm still imports
its own handler inside the call.

Error mapping: a missing method raises
:class:`MethodNotFoundError` (mapped to -32601 by the server); a
taxonomy ``ValidationError`` (bad numeric param, issue #238) maps to
-32602; any other error maps to -32603 by the server's one catch.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.api._socket_arms import (
    _add_entity,
    _add_relation,
    _analysis_select_method,
    _extraction_tune,
    _ingest_file,
    _ingest_text,
    _labeling_triage,
    _list_documents,
    _nli_classify,
    _query,
    _search,
    _verify_answer,
)
from corpus_kb.api._socket_rows import _SPECS
from corpus_kb.api._socket_specs import Arm, _route_arms, _spec_arm
from corpus_kb.contracts.model import DEFAULT_TENANT_ID as DEFAULT_TENANT


class MethodNotFoundError(Exception):
    """Raised by dispatch when no arm exists for the requested method."""


# Hand arms: bespoke coercion and hand-built envelopes (_socket_arms).
_HAND_ARMS: dict[str, Arm] = {
    "ingest_file": _ingest_file,
    "ingest_text": _ingest_text,
    "labeling_triage": _labeling_triage,
    "analysis_select_method": _analysis_select_method,
    "extraction_tune": _extraction_tune,
    "nli_classify": _nli_classify,
    "search": _search,
    "query": _query,
    "list_documents": _list_documents,
    "add_entity": _add_entity,
    "verify_answer": _verify_answer,
    "add_relation": _add_relation,
}

# Built once at import: core arms win a name clash with a route arm
# (same precedence the old lazy cache had), then dispatch never rebuilds.
_CORE_ARMS: dict[str, Arm] = {name: _spec_arm(spec) for name, spec in _SPECS.items()} | _HAND_ARMS
_ARM_TABLE: dict[str, Arm] = _route_arms() | _CORE_ARMS


def arms() -> dict[str, Arm]:
    """The full socket method table (core arms + generated route arms)."""
    return _ARM_TABLE


async def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one socket method; raise MethodNotFoundError when unknown."""
    arm = arms().get(method)
    if arm is None:
        raise MethodNotFoundError(method)
    tenant_id = str(params.get("tenant_id", DEFAULT_TENANT))
    return await arm(params, tenant_id)

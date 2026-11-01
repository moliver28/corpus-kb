"""Defaults-table lockstep gate (issue #238).

The catalogue rows are THE defaults table for cross-surface numeric and
scalar params: ``ParamSpec(name, default)`` must match the tool-layer
function signature default that owns the value. Tool-backed rows only
(handler strings under ``corpus_kb.tools.``) — handler-paired rows ride
typed request models, not signature defaults.

``search`` is the one deliberate exception: the catalogue pins k=20 (the
effective default, mirrored to ``reranking.return_top_k`` by the
lockstep test in ``test_config_blocks``) while the tool signature keeps
``k=None`` — the None-contract that lets an omitted k ride the config.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from corpus_kb.contracts import OPERATIONS
from corpus_kb.contracts.model import Operation

# (op name, param name): the catalogue default is the EFFECTIVE default
# (pinned to config elsewhere), while the tool signature keeps the
# None-contract "caller did not say; the handler decides".
EFFECTIVE_DEFAULT_OPS: dict[tuple[str, str], str] = {
    ("search", "k"): "reranking.return_top_k lockstep lives in test_config_blocks",
}


@pytest.mark.parametrize("op", OPERATIONS, ids=lambda op: op.name)
def test_tool_signature_matches_catalogue_defaults(op: Operation) -> None:
    if not op.handler.startswith("corpus_kb.tools."):
        pytest.skip("handler-paired row: defaults ride the typed request model")
    module_name, fn_name = op.handler.split(":")
    fn = getattr(importlib.import_module(module_name), fn_name)
    signature = inspect.signature(fn)
    for param in op.params:
        if param.required or param.default is None:
            continue
        assert param.name in signature.parameters, (
            f"{op.name}.{param.name}: catalogue param absent from the tool signature"
        )
        if (op.name, param.name) in EFFECTIVE_DEFAULT_OPS:
            continue
        tool_default = signature.parameters[param.name].default
        assert tool_default == param.default, (
            f"{op.name}.{param.name}: catalogue default {param.default!r} "
            f"!= tool signature default {tool_default!r}"
        )

"""Arm builders for the socket method table (spec + route shapes).

Two generated arm shapes share this module's plumbing:

  * Spec arms: built from a :class:`_Spec` row — one lazy tool import,
    one pool fetch, and strict parameter coercion (issue #238 helpers).
  * Route arms: built from a ``research_http.ToolRoute`` row, so the
    socket surface tracks the HTTP surface for free.

``socket_ops`` owns the rows and builds the dispatch table once at
import; nothing here runs per dispatch.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast

from corpus_kb.api._body import opt_bool, opt_float, opt_int, opt_str
from corpus_kb.api.research_http import ToolRoute

Arm = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class _Opt:
    """One arm parameter: socket name, coercion kind, and default.

    Kinds: ``req`` (required positional string), ``sdef`` (string with a
    default), ``str`` (opt_str), ``int``/``float``/``bool`` (the strict
    numeric coercion helpers, issue #238), ``raw`` (pass-through value).
    """

    name: str
    kind: str
    default: str | int | float | bool | None = None


@dataclass(frozen=True)
class _Spec:
    """One mechanical arm: import target plus pool and parameter layout.

    ``pool_pos`` is the positional slot for the research pool; set
    ``pool_kw`` instead when the tool takes the pool by keyword.
    """

    module: str
    fn: str
    pool_pos: int = 0
    pool_kw: str | None = None
    positional: tuple[_Opt, ...] = ()
    opts: tuple[_Opt, ...] = ()


def _spec_arm(spec: _Spec) -> Arm:
    """Build one mechanical arm: lazy import, pool fetch, strict params."""

    async def arm(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        from corpus_kb.api.research_http import research_pool

        fn = getattr(import_module(spec.module), spec.fn)
        pool = await research_pool()
        args: list[object] = []
        for row in spec.positional:
            if row.kind == "req":
                args.append(str(params[row.name]))
            else:
                args.append(str(params.get(row.name, row.default)))
        kwargs: dict[str, object] = {"tenant_id": tenant_id}
        for row in spec.opts:
            if row.kind == "sdef":
                kwargs[row.name] = str(params.get(row.name, row.default))
            elif row.kind == "raw":
                kwargs[row.name] = params.get(row.name)
            elif row.kind == "str":
                kwargs[row.name] = opt_str(params.get(row.name))
            elif row.kind == "int":
                kwargs[row.name] = opt_int(
                    params.get(row.name), cast("int | None", row.default), name=row.name
                )
            elif row.kind == "float":
                kwargs[row.name] = opt_float(
                    params.get(row.name), cast("float | None", row.default), name=row.name
                )
            else:
                kwargs[row.name] = opt_bool(params.get(row.name), cast("bool", row.default))
        if spec.pool_kw is not None:
            kwargs[spec.pool_kw] = pool
        else:
            args.insert(spec.pool_pos, pool)
        return await fn(*args, **kwargs)

    return arm


def _route_arm(route: ToolRoute) -> Arm:
    """Build one socket arm from a ``research_http.ToolRoute`` row."""

    async def arm(params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        from corpus_kb.api.research_http import research_pool

        kwargs: dict[str, Any] = {key: params[key] for key in route.required}
        for key in route.optional:
            if key in params:
                kwargs[key] = params[key]
        if "tenant_id" in inspect.signature(route.fn).parameters:
            kwargs["tenant_id"] = tenant_id
        if route.pool_kw is not None:
            kwargs[route.pool_kw] = await research_pool()
        outcome = route.fn(**kwargs)
        if inspect.isawaitable(outcome):
            return cast("dict[str, Any]", await outcome)
        return cast("dict[str, Any]", outcome)

    return arm


# The notebook tool functions end in ``_tool``; the socket method (and the
# MCP tool name) does not.
_METHOD_FOR_TOOL: dict[str, str] = {
    "notebook_ask_tool": "notebook_ask",
    "notebook_evidence_tool": "notebook_evidence",
    "notebook_uncoded_tool": "notebook_uncoded",
    "notebook_overlap_tool": "notebook_overlap",
}


def _route_arms() -> dict[str, Arm]:
    from corpus_kb.api.research_http import tool_routes

    arms: dict[str, Arm] = {}
    for route in tool_routes():
        name = _METHOD_FOR_TOOL.get(route.fn.__name__, route.fn.__name__)
        arms[name] = _route_arm(route)
    return arms

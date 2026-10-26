"""MCP tool registration mechanics (zero-drift Wave 4A).

The registry-generated MCP surface needs, per operation: the shared
function to call, the pool/config injection, and the exposed parameter
list. This module owns those mechanics; ``mcp_server.py`` owns the server
object and the stdio entry point.

Two sources feed one generator:

  * The parity catalogue (``corpus_kb.contracts``): every row with an MCP
    surface gets a tool. The catalogue is the single source — a new row
    with ``SurfaceSpec(MCP, ...)`` automatically gains an MCP surface. The
    few rows whose ``handler`` string names the DEEPER handler (the
    release/research/notebook families) resolve to the tool-layer function
    through ``TOOL_LAYER_OVERRIDES``; that tool layer is the ONE shared
    function the HTTP surface already routes through
    (``api/research_http.py``).
  * ``surface_registry.SURFACES``: research/coding rows outside the
    catalogue. ``REGISTRY_TOOL_FUNCTIONS`` maps each MCP tool name to its
    tool-layer function; a registry row without a function fails the
    build, so the old "declared but missing tool" state cannot return.

The wrapper exposes ONLY the catalogue's parameters (or, for registry
tools, the function's own minus pool/config). Pool and config are
injected, never advertised. The JSON schema comes from the shared
function's real signature, with catalogue defaults winning where the row
pins the effective default (the W3 search/query ``k``), so the advertised
tool cannot drift from the tested code or the catalogue.

Tool modules load lazily (importlib inside the build), keeping this
module's import cost at stdlib + mcp + contracts.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, get_type_hints

from mcp.server.mcpserver import MCPServer

from corpus_kb.contracts import OPERATIONS
from corpus_kb.contracts.model import MCP, Operation, surfaces_of

# ---------------------------------------------------------------------------
# The zero-exclusion rule, with the one place to deviate
# ---------------------------------------------------------------------------

# Ops that cannot run server-side as-is. EMPTY by design (program rule:
# prefer zero exclusions). Every entry MUST carry an honest reason; the
# tests fail when a reason is missing, and the skipped tool must stay
# documented in docs/FEATURES.md.
EXCLUDED_TOOLS: dict[str, str] = {}

# Catalogue rows whose ``handler`` string names the deeper handler, mapped
# to the tool-layer function every surface routes through. Keys are
# catalogue operation names; values are "module:function" strings.
TOOL_LAYER_OVERRIDES: dict[str, str] = {
    "release_assess": "corpus_kb.tools.release_tools:release_assess",
    "release_request": "corpus_kb.tools.release_tools:release_request",
    "release_review": "corpus_kb.tools.release_tools:release_review",
    "release_execute": "corpus_kb.tools.release_tools:release_execute",
    "release_status": "corpus_kb.tools.release_tools:release_status",
    "release_compare": "corpus_kb.tools.release_tools:release_compare",
    "research_ingest_transcript": "corpus_kb.tools.research_tools:research_ingest_transcript",
    "notebook_ask": "corpus_kb.tools.notebook_tools:notebook_ask_tool",
    # Full-surface parity wave: rows whose handler names the deeper
    # handler, mapped to the tool layer the HTTP route mirrors.
    "query": "corpus_kb.tools.query_tools:query",
    "verify_answer": "corpus_kb.tools.query_tools:verify_answer",
    "add_entity": "corpus_kb.tools.graph_tools:add_entity",
    "add_relation": "corpus_kb.tools.graph_tools:add_relation",
}

# surface_registry MCP tool names OUTSIDE the catalogue, mapped to their
# tool-layer functions. The release/ingest/research rows the catalogue
# already covers are absent: the catalogue leg registers them.
REGISTRY_TOOL_FUNCTIONS: dict[str, str] = {
    "research_ingest": "corpus_kb.tools.research_tools:research_ingest",
    "coding_run": "corpus_kb.tools.research_tools:coding_run",
    "inductive_run": "corpus_kb.tools.research_tools:inductive_run",
    "codebook_promote": "corpus_kb.tools.research_tools:codebook_promote",
    "research_report": "corpus_kb.tools.research_tools:research_report",
    "review_execute": "corpus_kb.tools.research_tools:review_execute",
    "notebook_evidence": "corpus_kb.tools.notebook_tools:notebook_evidence_tool",
    "notebook_uncoded": "corpus_kb.tools.notebook_tools:notebook_uncoded_tool",
    "notebook_overlap": "corpus_kb.tools.notebook_tools:notebook_overlap_tool",
    "research_cycle": "corpus_kb.tools.cycle_tools:research_cycle",
    # UI SPA Task 5b (issue #176): the lifecycle UI ops. The wrappers bind
    # the ONE handler each surface already routes through — the doctor
    # snapshot the HTTP /api/doctor route serves, and the demo loader the
    # CLI demo-kb command and POST /api/demo/load share.
    "doctor": "corpus_kb.tools.doctor_tools:doctor_snapshot",
    "demo_load": "corpus_kb.demo_kb:load_demo_kb",
}

_POOL_PARAM_NAMES = ("pg_pool", "pool")


def resolve_function(spec: str) -> Any:
    """Importlib-resolve one ``module:function`` handler string."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not attr:
        raise ValueError(f"Handler string must be 'module:function', got {spec!r}")
    module = importlib.import_module(module_name)
    fn = getattr(module, attr)
    if not callable(fn):
        raise ValueError(f"Handler {spec!r} is not callable")
    return fn


def _pool_param(fn: Any) -> str | None:
    """The function's pool parameter name (``pg_pool``/``pool``), or None."""
    for name in _POOL_PARAM_NAMES:
        if name in inspect.signature(fn).parameters:
            return name
    return None


def _catalogue_exposed(op: Operation, fn: Any) -> tuple[str, ...]:
    """The catalogue's ParamSpec list, validated against the function.

    A catalogue parameter the function does not take, or a required one
    the function defaults, is catalogue drift: fail the build, never
    publish a tool that cannot keep the contract.
    """
    parameters = inspect.signature(fn).parameters
    exposed: list[str] = []
    for spec in op.params:
        if spec.name not in parameters:
            raise ValueError(
                f"Catalogue drift on {op.name!r}: parameter {spec.name!r} is not a "
                f"parameter of {op.handler!r}"
            )
        if spec.required and parameters[spec.name].default is not inspect.Parameter.empty:
            raise ValueError(
                f"Catalogue drift on {op.name!r}: {spec.name!r} is required in the "
                f"catalogue but defaulted by {op.handler!r}"
            )
        exposed.append(spec.name)
    return tuple(exposed)


def _function_exposed(fn: Any, pool_name: str | None) -> tuple[str, ...]:
    """The function's own parameters minus the injected pool and config."""
    skip = {"config"}
    if pool_name is not None:
        skip.add(pool_name)
    return tuple(name for name in inspect.signature(fn).parameters if name not in skip)


def build_wrapper(
    fn: Any,
    pool: Any,
    config: Any,
    exposed: tuple[str, ...],
    name: str,
    catalogue_defaults: dict[str, object] | None = None,
) -> Any:
    """Wrap one shared function into an MCP tool callable.

    The wrapper's signature carries only the exposed parameters, with the
    shared function's real annotations and defaults. Pool and config are
    bound in the closure and never advertised. The result is a plain dict
    (the envelope); ``structured_output=False`` keeps the wire shape a
    JSON text block.

    ``catalogue_defaults`` (catalogue tools only) overrides a signature
    default for the ADVERTISED schema when the catalogue row carries the
    effective default: the W3 ``search``/``query`` ``k`` resolves an
    omitted value from ``reranking.return_top_k`` at runtime, so the
    function signature is ``None`` while the catalogue pins 20. The
    runtime behavior still comes from the shared function; only the
    advertised default follows the catalogue.
    """
    signature = inspect.signature(fn)
    # get_type_hints resolves the tools' string annotations (future
    # annotations) against the shared function's own module globals.
    hints = get_type_hints(fn)
    defaults = catalogue_defaults or {}
    parameters = []
    for param_name in exposed:
        if param_name not in signature.parameters:
            raise ValueError(
                f"Tool {name!r}: exposed parameter {param_name!r} is not a parameter "
                f"of the shared function"
            )
        default = signature.parameters[param_name].default
        if param_name in defaults:
            default = defaults[param_name]
        parameters.append(
            inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=hints.get(param_name, str),
            )
        )
    pool_name = _pool_param(fn)
    takes_config = "config" in signature.parameters

    async def _call(**kwargs: Any) -> dict[str, object]:
        call_args: dict[str, Any] = dict(kwargs)
        if pool_name is not None:
            call_args[pool_name] = pool
        if takes_config:
            call_args["config"] = config
        result = fn(**call_args)
        if inspect.isawaitable(result):
            result = await result
        return result

    _call.__name__ = name
    _call.__qualname__ = f"mcp_tool_{name}"
    _call.__doc__ = getattr(fn, "__doc__", None)
    # The SDK reads the advertised signature with inspect.signature();
    # setattr keeps the dynamic attribute assignment type-honest.
    _call.__signature__ = inspect.Signature(parameters, return_annotation=dict)
    return _call


def register_catalogue_tools(server: MCPServer, pool: Any, config: Any) -> list[str]:
    """Register one tool per catalogue operation with an MCP surface."""
    registered: list[str] = []
    for op in OPERATIONS:
        mcp = surfaces_of(op, MCP)
        if not mcp:
            continue
        tool_name = mcp[0].route
        if tool_name in EXCLUDED_TOOLS:
            continue
        fn = resolve_function(TOOL_LAYER_OVERRIDES.get(op.name, op.handler))
        exposed = _catalogue_exposed(op, fn)
        defaults = {
            spec.name: spec.default
            for spec in op.params
            if not spec.required and spec.default is not None
        }
        server.add_tool(
            build_wrapper(fn, pool, config, exposed, tool_name, catalogue_defaults=defaults),
            name=tool_name,
            description=op.description,
            structured_output=False,
        )
        registered.append(tool_name)
    return registered


def register_registry_tools(
    server: MCPServer, pool: Any, config: Any, already: tuple[str, ...] = ()
) -> list[str]:
    """Register the surface_registry MCP rows outside the catalogue.

    A registry row with an MCP tool name but no function fails the build:
    "declared but does not exist" is the lie this wave removes.
    """
    registered: list[str] = []
    seen = set(already) | set(EXCLUDED_TOOLS)
    from corpus_kb.surface_registry import SURFACES

    for surface in SURFACES:
        if not surface.mcp_tool or surface.mcp_tool in seen:
            continue
        spec = REGISTRY_TOOL_FUNCTIONS.get(surface.mcp_tool)
        if spec is None:
            raise ValueError(
                f"surface_registry tool {surface.mcp_tool!r} has no function "
                f"mapping in mcp_tools.REGISTRY_TOOL_FUNCTIONS"
            )
        fn = resolve_function(spec)
        exposed = _function_exposed(fn, _pool_param(fn))
        server.add_tool(
            build_wrapper(fn, pool, config, exposed, surface.mcp_tool),
            name=surface.mcp_tool,
            description=surface.description,
            structured_output=False,
        )
        registered.append(surface.mcp_tool)
        seen.add(surface.mcp_tool)
    return registered

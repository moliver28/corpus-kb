"""Vendor-neutral OpenTelemetry tracing (UX program Wave 4).

Design contract:

- NO-OP BY DEFAULT: ``opentelemetry-api`` is a core dependency and a
  no-op tracer without the SDK. Every helper below is safe (and free)
  when ``observability.enabled`` is false — spans are non-recording and
  ``current_trace_id()`` returns None, so envelopes never grow a key.
- LAZY SDK: ``opentelemetry.sdk`` and the OTLP HTTP exporter live in the
  ``[otel]`` extra (CI never installs it). They are imported only inside
  :func:`setup_tracing` through the one import seam; a missing extra is
  a typed :class:`OtelDependencyError`, never an ImportError. The seam
  and the typed error follow the ``topics/engine.py`` house pattern.
- SPANS: the names below are OUR vocabulary (snake_case, stable). Call
  sites wrap existing code with the :func:`span` context manager; logic
  is never restructured to record a span.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from opentelemetry import trace

from corpus_kb.errors import DependencyMissingError

logger = logging.getLogger(__name__)

OTEL_EXTRA_REMEDIATION = "pip install 'corpus-kb[otel]'"

TRACER_NAME = "corpus_kb"
DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"
DEFAULT_SERVICE_NAME = "corpus-kb"
_OTLP_TRACES_PATH = "/v1/traces"

# ---------------------------------------------------------------------------
# Span names (our vocabulary — snake_case, stable, self-owned).
# ---------------------------------------------------------------------------

# Operation roots.
SPAN_QUERY = "corpus_kb.query"
SPAN_ROUTE = "corpus_kb.route"
SPAN_VERIFY_ANSWER = "corpus_kb.verify_answer"
SPAN_ASK = "corpus_kb.ask"
SPAN_INGEST = "corpus_kb.ingest"

# Query pipeline stages (children of corpus_kb.query).
SPAN_ROUTE_DECIDE = "route.decide"
SPAN_RETRIEVE_VECTOR = "retrieve.vector"
SPAN_RETRIEVE_DEGRADED_PROBE = "retrieve.degraded_probe"
SPAN_RETRIEVE_FTS = "retrieve.fts"
SPAN_RRF_FUSION = "rrf_fusion"
SPAN_RERANK = "rerank"
SPAN_EXPAND_PARENT = "expand_parent"

# Ask pipeline stages (children of corpus_kb.ask).
SPAN_ASK_RETRIEVE = "retrieve"
SPAN_ASK_GENERATE = "generate"
SPAN_ASK_VERIFY = "verify"

# Ingest pipeline stages (children of corpus_kb.ingest).
SPAN_PARTITION = "partition"
SPAN_CHUNK = "chunk"
SPAN_EMBED = "embed"
SPAN_EXTRACT = "extract"
SPAN_STORE = "store"

# ---------------------------------------------------------------------------
# Span attributes (corpus_kb.* namespace — data already in hand, no new I/O).
# ---------------------------------------------------------------------------

ATTR_ROW_COUNT = "corpus_kb.row_count"
ATTR_MODEL = "corpus_kb.model"
ATTR_CHUNK_COUNT = "corpus_kb.chunk_count"
ATTR_ELEMENT_COUNT = "corpus_kb.element_count"
ATTR_STRATEGY = "corpus_kb.strategy"
ATTR_EMBEDDING_MODEL = "corpus_kb.embedding_model"
ATTR_EMBEDDING_MODE = "corpus_kb.embedding_mode"
ATTR_BATCH_SIZE = "corpus_kb.batch_size"
ATTR_RRF_K = "corpus_kb.rrf_k"
ATTR_FUSED_COUNT = "corpus_kb.fused_count"
ATTR_RERANK_MODEL = "corpus_kb.rerank_model"
ATTR_CANDIDATES_IN = "corpus_kb.candidates_in"
ATTR_CANDIDATES_OUT = "corpus_kb.candidates_out"
ATTR_DEGRADED = "corpus_kb.degraded"
ATTR_EXCLUDED_COUNT = "corpus_kb.excluded_count"
ATTR_ROUTE = "corpus_kb.route"
ATTR_CONFIDENCE = "corpus_kb.confidence"
ATTR_FALLBACK = "corpus_kb.fallback"
ATTR_CLAIMS = "corpus_kb.claims"
ATTR_GROUNDEDNESS = "corpus_kb.groundedness"
ATTR_EXTRACT_BACKEND = "corpus_kb.extract_backend"
ATTR_ENTITY_COUNT = "corpus_kb.entity_count"
ATTR_RELATION_COUNT = "corpus_kb.relation_count"
ATTR_SOURCE_TYPE = "corpus_kb.source_type"

# One span attribute value (the OTel primitive set; None = "drop this one").
AttrValue = str | bool | int | float


class OtelDependencyError(DependencyMissingError):
    """The ``[otel]`` extra is not installed."""

    remediation = OTEL_EXTRA_REMEDIATION


_provider_set = False


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def _observability_block(cfg: dict[str, object]) -> dict[str, object]:
    """The ``observability`` mapping, or an empty one."""
    block = cfg.get("observability")
    if isinstance(block, dict):
        return block
    return {}


def setup_tracing(cfg: dict[str, object]) -> bool:
    """Install the OTLP tracer provider when ``observability.enabled`` is true.

    Disabled (the default) touches nothing and returns False — the no-op
    API tracer keeps every span free. Enabled, it lazy-imports the SDK
    and the OTLP HTTP exporter; a missing extra raises the typed
    :class:`OtelDependencyError` (callers catch it and log one line —
    tracing setup must never break server startup). A second call is a
    no-op returning True (the provider installs once per process).
    """
    global _provider_set
    block = _observability_block(cfg)
    if not bool(block.get("enabled", False)):
        return False
    if _provider_set:
        return True
    try:
        sdk_trace = _import_module("opentelemetry.sdk.trace")
        sdk_resources = _import_module("opentelemetry.sdk.resources")
        exporter_module = _import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    except ModuleNotFoundError as exc:
        raise OtelDependencyError(
            "Tracing needs the opentelemetry SDK and OTLP exporter (the [otel] extra)."
        ) from exc
    service_name = str(block.get("service_name", DEFAULT_SERVICE_NAME))
    endpoint = str(block.get("otlp_endpoint", DEFAULT_OTLP_ENDPOINT)).rstrip("/")
    if not endpoint.endswith(_OTLP_TRACES_PATH):
        endpoint = f"{endpoint}{_OTLP_TRACES_PATH}"
    resource = sdk_resources.Resource.create({"service.name": service_name})
    provider = sdk_trace.TracerProvider(resource=resource)
    exporter = exporter_module.OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(sdk_trace.BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider_set = True
    logger.info("Tracing enabled: OTLP spans to %s (service %s)", endpoint, service_name)
    return True


def shutdown_tracing() -> None:
    """Best-effort provider shutdown (flush pending spans, stop the worker).

    Never raises; safe to call twice and safe to call when tracing was
    never enabled (the no-op path returns immediately).
    """
    global _provider_set
    if not _provider_set:
        return
    _provider_set = False
    shutdown = getattr(trace.get_tracer_provider(), "shutdown", None)
    if not callable(shutdown):
        return
    try:
        shutdown()
    except Exception as exc:
        logger.warning("Tracing shutdown failed: %s", exc)


def current_trace_id() -> str | None:
    """The active span's 32-char hex trace id, or None.

    Works with the no-op API: without a recording span the context is
    invalid and None is returned — this is the gate that keeps
    ``trace_id`` out of every envelope when tracing is off.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


@contextmanager
def span(name: str, /, **attributes: AttrValue | None) -> Iterator[trace.Span]:
    """One-line span wrapper: ``with span(SPAN_X, attr=value): ...``.

    Attributes whose value is None are dropped (OTel has no null
    attribute); everything else must be a primitive. The active span is
    yielded so call sites can record values known only after the work
    (counts, model names). Without the SDK this is a non-recording
    no-op.
    """
    with trace.get_tracer(TRACER_NAME).start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current

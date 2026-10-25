# Observability

Corpus-KB ships OpenTelemetry spans for the ingest and query pipelines. Any OTLP backend can read them.

## What gets traced

Every operation opens a root span. Stages become child spans.

| Operation | Root span | Children |
|---|---|---|
| Search | `corpus_kb.query` | `retrieve.vector`, `retrieve.degraded_probe`, `retrieve.fts`, `rrf_fusion`, `rerank`, `expand_parent` |
| Routed query | `corpus_kb.route` | `route.decide` |
| Ask | `corpus_kb.ask` | `retrieve`, `generate`, `verify` |
| Ingest | `corpus_kb.ingest` | `partition`, `chunk`, `embed`, `extract`, `store` |
| Verify answer | `corpus_kb.verify_answer` | — |

Spans carry attributes from data in hand. Examples: row counts, chunk counts, model names, `rrf_k`, route, and confidence. Tracing never adds I/O.

Strategies fuse parsing and chunking into one call. The `partition` span covers strategy resolution and parsing. The `chunk` span covers the plan-to-chunk conversion.

## Zero cost when off

Tracing is off by default. The `opentelemetry-api` package acts as a no-op tracer without the SDK. No span records, and no envelope gains a key.

## Enable tracing

Install the extra:

```bash
pip install 'corpus-kb[otel]'
```

Set the config block in `config.yaml`:

```yaml
observability:
  enabled: true
  otlp_endpoint: "http://localhost:4318"
  service_name: "corpus-kb"
```

`otlp_endpoint` is the OTLP/HTTP base URL. The exporter appends `/v1/traces` to it. The env var `CORPUS_KB_OTLP_ENDPOINT` overrides the config value.

A missing extra or a broken endpoint never blocks startup. The server logs one line and continues with tracing off.

## Jaeger quickstart

Jaeger v2 ships as one binary. Download it, run it, and open the UI.

```bash
# 1. Download a release from https://jaegertracing.io/download/ for your OS.
# 2. Run it (the default config enables OTLP on 4317/4318 and the UI):
./jaeger
# 3. Open http://localhost:16686 and pick the "corpus-kb" service.
```

## Other backends

Two expert options also accept OTLP spans:

- Arize Phoenix: tracing built for LLM apps. License: Elastic-2.0.
- Langfuse: LLM tracing UI. The core is MIT; some enterprise folders differ.

Both document their own OTLP ingest endpoints. Point `otlp_endpoint` at the collector each one runs.

## The `trace_id` envelope field

With tracing on, the search envelope adds a `trace_id` key. Paste it into the Jaeger search box to find the exact trace. The key is absent when tracing is off.

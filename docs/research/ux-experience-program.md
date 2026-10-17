# UX experience program — the ideal-state design (researched)

Status: RESEARCHED, NOT STARTED (2026-10-13).
Inputs: repo audit at master `c6b8770`; two web-research passes with
primary-source verification (licenses, versions, maintenance posture) on
2026-10-13. Evidence caveats are inline where a claim rests on a page
summary rather than package JSON.

---

## 0. Goal ledger

Every wave must trace to one of these. A change that serves no goal is out.

| ID | Goal | Done when |
|----|------|-----------|
| G1 | Novice browser path | Install, one command, then add knowledge and ask questions with citations in a browser. No YAML, no SQL, no terminal beyond install. |
| G2 | Universal interop | Any OpenAI-compatible client can use corpus-kb as its RAG backend: `/v1/chat/completions` (streaming) and `/v1/embeddings`. |
| G3 | Deep transparency | Every ingest and query emits an OpenTelemetry trace, viewable in any OTLP backend. Zero new required dependencies. |
| G4 | Offline evaluation | Golden sets plus answer metrics (faithfulness, answer relevancy, contextual recall/precision) run fully local with an Ollama judge. |
| G5 | Researcher console | marimo notebooks served by the same server, with shipped examples for topics, ablation, and eval. |
| G6 | Elegance floor | No Node build chain. No second RAG stack. No vendor SDK in core. Every new surface is registry-driven and parity-tested. |
| G7 | Honest UX | Degraded modes (`embedding_mode`, verify abstentions, missing models) are visible in UI and API envelopes, never silent. |

---

## 1. Current state (audited 2026-10-13)

Verified facts at master `c6b8770`, CI 14/14 green:

- 331 Python modules, ~61k LOC, 204 test files. Solo-developer scale is a
  design constraint: every added surface must amortize, not multiply.
- Four surfaces (MCP stdio, HTTP, socket, CLI) share 34 operations through
  the surface registry, with a parity gate and a contract snapshot in CI.
- The HTTP surface is JSON-only: ~30 routes, error-envelope discipline,
  CORS enabled. No static files, no HTML, no SSE, no browser open. Grep
  confirmed: zero `StaticFiles`, `text/html`, `text/event-stream` hits.
- CLI: 25+ commands including `quickstart`, `doctor`, `ingest` (with a
  watched drop folder), `ingest-status`, `search`, `query`, `verify-answer`,
  `topics-build/list`, and the research/coding/release domain groups.
- Packaging: the optional-extra pattern is mature (`latechunk`,
  `inductive`, `ner`, `topics`). Extras are lazy-imported; CI stays green
  without any extra installed. This is the template for every new extra.
- Evaluation: `research/retrieval_eval.py` computes recall@k, MRR, nDCG@10,
  and p95 latency, but sits at `insufficient_data` — no frozen labeled set.
  `verify-answer` does claim-level checking. There is no answer-quality
  metric (faithfulness, answer relevancy) and no golden-set builder for
  general corpora.
- Observability: none. Envelopes carry `embedding_mode` and degradation
  notes, but there is no per-stage timing or trace anywhere.
- First-run: `quickstart` (merged #114) takes a fresh clone to a first
  successful search. The default config DSN still points at the dead
  compose port 5433; non-quickstart paths need an env var today.
- The demo corpus (`docs/demo-corpus/`) serves the qualitative-coding
  domain. A general RAG demo set does not exist.

### The honest gap

corpus-kb is an excellent engine with a single front door: a terminal.
A novice cannot use it without learning five concepts (venv, Postgres,
env var, migrations, config). An expert cannot plug it into the tools
they already run, because it speaks no standard protocol. Both gaps are
closing moves, not rebuilds.

---

## 2. The vision — two users, one engine

### 2.1 Maya, novice analyst

1. `pip install corpus-kb[ui]` then `corpus-kb start`. The terminal prints
   one URL and the browser opens on it. That is the whole setup.
2. The home screen checks her system in plain language (doctor's checks,
   rendered kindly): "Ollama is running. Database ready. 0 of 2 models
   downloaded — click to fetch." Each failure shows the exact fix command.
3. "Add knowledge" accepts drag-and-drop files, a folder, or a URL. She
   watches per-file progress in plain words: "Reading... Splitting...
   Embedding 42/158... Done." The honest envelope renders as status.
4. She asks questions in a chat box. Answers carry numbered citations that
   link to the source document. A badge shows whether every claim
   verified, and abstains honestly when it did not.
5. The Library page lists her documents, topics (BERTopic already exists),
   and entity graph summaries. Delete and re-ingest are one click.

She never learns that Postgres, pgvector, RRF, or halfvec exist.

### 2.2 Ethan, expert researcher

1. Everything Maya has, plus the API surface he already knows: he points
   an OpenAI SDK, LangChain, vLLM tooling, or any chat frontend at
   `http://localhost:8010/v1`. corpus-kb becomes infrastructure.
2. Every query produces a trace: retrieval arms with candidate counts,
   RRF fusion with k, rerank scores in and out, token counts from the
   answer model, verify verdicts. He opens Jaeger (one binary) or Phoenix.
3. He builds a golden set from his own corpus, then runs faithfulness and
   retrieval metrics offline with a local judge model. Config and score
   land in one report; ablations become one config diff.
4. He opens a marimo notebook served from the same process to explore
   embeddings, topics, and eval outputs. Notebooks are `.py` files under
   git, reproducible by construction.
5. He still has the full CLI, MCP, HTTP, and socket surface — unchanged.

### 2.3 The one-sentence test

corpus-kb should feel like "your private, verifiable knowledge engine":
point it at files, ask it anything, see the proof, and script it with
whatever you already use.

---

## 3. Researched decisions (evidence-dated 2026-10-13)

### D1 — Expose an OpenAI-compatible API surface (G2). DECIDED.

Custom OpenAI-compatible base URL is the universal 2026 integration
pattern. All seven surveyed frontends accept one: Open WebUI 0.12.3,
LibreChat 0.8.8, AnythingLLM, Jan (Apache-2.0), big-AGI, Hollama,
LobeChat. Local serving stacks all expose the same shape (Jan at
`127.0.0.1:1337`, vLLM, Ollama). Build:

- `POST /v1/chat/completions` — non-streaming plus SSE streaming
  (`data:` chunks and `data: [DONE]`). Answers come from the existing
  query/verify path. Citations render as a markdown Sources block inside
  the message body — the only portable convention (no citation field
  exists in the chat schema).
- `POST /v1/embeddings` — the corpus embedder (including late chunking),
  usable by external tools.
- `GET /v1/models` — the configured answer/embedding model names.
- Non-goals: tool calling, multimodal, usage billing fields beyond `usage`.
- Gated by `server.openai_compat` (default on). Core package, pydantic
  models only, no new runtime dependency.

### D2 — First-party novice UI: NiceGUI behind `[ui]` (G1). DECIDED.

| Option | Verdict |
|--------|---------|
| NiceGUI 3.5.0, MIT, Py>=3.9 | **Chosen.** First-class FastAPI mount (`ui.run_with(app)`), typed, ships prebuilt assets (no Node), async handlers, chat/upload/progress widgets built in. |
| Chainlit 2.12.0, Apache-2.0 | Best chat UX, but "community-maintained as of May 2025" — do not build a core surface on it. Documented future swap. |
| Gradio 6.x, Apache-2.0 | Mountable, active, but ~31 MB wheel, demo aesthetic. Fallback only. |
| Panel 1.9.4, BSD-3 | Production chat widgets; Tornado/Bokeh machinery heavier than needed. |
| FastHTML 0.14.13, Apache-2.0 | Max control, most hand-rolled code for chat. |
| Mesop | **Dead** — Google archived the repo 2026-10-02. Excluded. |
| Reflex | Compiles to React with a Node-managed build. Violates G6. Excluded. |
| Open WebUI as bundled UI | License is BSD-3 **plus a branding clause** (not OSI; 50-user threshold; enterprise license for rebrand). Fine to point AT, wrong to build ON. |
| LobeChat | Apache-2.0 plus additional commercial conditions. Not clean. |
| GPT4All | Dormant since 2025-02-24. Excluded. |

Shape: pages `Overview` (plain-language doctor + fixes), `Add` (upload,
URL, watched-folder status, progress), `Ask` (chat with citations, verify
badge, expert tuning panel), `Library` (documents, topics, entities,
delete/re-ingest). View-model functions live in tested modules under
`src/corpus_kb/ui/`; the NiceGUI layer stays thin. One process, one port,
zero new daemons. Browser auto-open on `start` when the extra is present.

### D3 — Tracing: OpenTelemetry spans, OpenInference vocabulary (G3). DECIDED.

- Core dependency: `opentelemetry-api>=1.45` only (Apache-2.0, one
  transitive). Without the SDK it is a no-op tracer — zero cost when off.
- Extra `[otel]`: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
  (HTTP, not gRPC — pure-Python, Windows-safe), and
  `openinference-semantic-conventions` 0.1.41 (Apache-2.0, zero deps) for
  stable RAG span attributes. Official OTel GenAI semconv are still
  experimental in 2026; OpenInference is the stable vocabulary today.
- Span plan. Ingest root `corpus_kb.ingest`: `partition` → `chunk`
  (strategy, count) → `embed` (model, dims, `embedding_mode`, per batch)
  → `extract_entities` (backend, counts) → `store` (txn, counts).
  Query root `corpus_kb.query`: `retrieve.vector` / `retrieve.fts` →
  `rrf_fusion` (k, fused count) → `rerank` (model, in/out counts) →
  `generate` (model, token counts from Ollama usage) → `verify_claims`.
- Envelopes gain `trace_id` when tracing is enabled.
- Backend guidance: Jaeger v2 (Apache-2.0, single binary, OTLP on 4318,
  UI on 16686) for everyone; Phoenix for LLM-aware spans; Langfuse for
  teams. License flags: **Phoenix switched to Elastic-2.0** (source-
  available, not OSI; self-host free) and **Langfuse was acquired by
  ClickHouse 2026-01-16** (core still MIT, `ee/` folders are not; self-host
  is a 6-container Docker stack). Both fine to point AT, neither becomes
  a dependency.

### D4 — Evaluation: DeepEval behind `[eval]` (G4). DECIDED.

| Option | Verdict |
|--------|---------|
| DeepEval 4.3.1, Apache-2.0 | **Chosen.** Official Ollama integration (offline, no key), metrics match the pipeline exactly (faithfulness, answer relevancy, contextual precision/recall/relevancy), golden-set `Synthesizer`, pytest-native. 32 deps; `openai` is a hard dep (offline-safe; note in deptry). |
| Ragas 0.4.3, Apache-2.0 | Works with Ollama via LangChain wrappers, but drags ~50 deps including the full LangChain stack. Optional import path only if its knowledge-graph test-set generation is wanted later. |
| TruLens 2.15.0, MIT | Snowflake-maintained, active; wrapper glue for Ollama; OTel overlap with D3. Keep as watchlist. |
| Giskard 3.0.1 | Requires Python >=3.12 — conflicts with the repo's 3.11 floor. Excluded. |
| promptfoo | MIT, good rubric model, but Node CLI. Violates G6. Excluded. README claims "part of OpenAI" (unverified date). |
| arize-phoenix-evals 3.9.2 | **Elastic-2.0 now.** Excluded from any extra. |

Shape: `corpus-kb eval build-golden` (from human-reviewed data or manual
JSONL) and `corpus-kb eval run` producing one honest report: retrieval
metrics from the existing harness plus answer metrics from DeepEval, all
with the local Ollama judge. Wire `research/retrieval_eval.py` outputs in
rather than reimplementing them.

### D5 — Researcher console: marimo behind `[notebook]` (G5). DECIDED.

marimo 0.25.1, Apache-2.0, reproducible `.py` notebooks, mounts into the
running server via `create_asgi_app().with_dynamic_directory(...)`.
Known wart: dynamic-directory refresh after edits (issue #5527) — restart
to reload is acceptable; document it. Ship three example notebooks:
topic exploration, retrieval ablation, eval report. Jupyter stays a
fallback the user brings themselves.

### D6 — Token accounting: in-house, no dependency. DECIDED.

Ollama returns real usage (`prompt_eval_count`/`eval_count`; `usage` on
the OpenAI-compatible path). Capture actuals as span attributes and
envelope fields. tiktoken would count the wrong tokenizer and downloads
vocab files on first use. ~50 lines, owned.

### D7 — Rejected wholesale: embedding someone else's RAG stack. DECIDED.

AnythingLLM, Open WebUI, and Jan each carry their own retrieval stack.
Used as chat skins over corpus-kb they work (D1); used as the product they
bypass everything corpus-kb is. The UI we own (D2) exists precisely so the
novice path shows corpus-kb's honesty features: degradation notes, verify
verdicts, provenance.

---

## 4. Elegance rules (how this stays cleaner than what came before)

1. **One answer path.** Wave 1 factors answer composition (answer text,
   sources, verify verdict, markdown citation rendering) into one shared,
   tested module. CLI, HTTP, UI, and the OpenAI adapter all render the
   same object. No surface owns prose assembly ever again.
2. **Adapters stay dumb.** UI pages and `/v1/*` routes call the same
   handlers the other surfaces call. New business logic appears once, in
   the handler layer, where the parity gate already watches.
3. **Extras stay optional and lazy.** `[ui]`, `[otel]`, `[eval]`,
   `[notebook]` follow the `latechunk`/`topics` contract: lazy imports,
   extras-less CI stays green, deptry reflects reality.
4. **No Node. No new ports. No new daemons.** Everything mounts into the
   existing Starlette app behind `corpus-kb start`.
5. **Honesty is the UI.** Degradation, abstention, and provenance render
   as first-class status, not console warnings. This is the product
   differentiator; protect it in code review.
6. **Size discipline.** New modules respect the 250-line soft limit; the
   UI package is several small modules, not one giant page file. xenon
   stays at C; pyright stays clean without new ignores.
7. **Docs split by persona.** One novice path (install → UI) and one
   expert path (API → otel → eval → notebook), cross-linked, no
   duplication of operational truth.

---

## 5. Wave plan

Order is load-bearing: W1 unblocks W2 and W3; nothing else is sequential.

| Wave | Delivers | Gates (all existing, plus) |
|------|----------|---------------------------|
| W1 | Shared answer envelope + citation composer; envelopes for `query`, `search_context`, `verify-answer` gain a stable `sources` shape | Contract snapshot updated; parity tests; unit tests for the composer (pure functions) |
| W2 | `/v1/models`, `/v1/chat/completions` (SSE + JSON), `/v1/embeddings`, `server.openai_compat` flag | Streaming parser tests (SSE frames, `[DONE]`); live smoke vs an OpenAI SDK client in CI optional job; config docs |
| W3 | `[ui]` NiceGUI: Overview / Add / Ask / Library; browser auto-open; demo-corpus loader button | UI view-model unit tests (no browser in CI); extras-less CI green; `poe verify` extended to import-guard the UI extra |
| W4 | `[otel]` spans per §D3; `trace_id` in envelopes; docs for Jaeger/Phoenix/Langfuse | No-op-tracer test (tracing off = zero spans, zero deps loaded); span-name snapshot test |
| W5 | `[eval]` golden-set builder + offline runner with Ollama judge; unified report with retrieval harness | Golden-set schema tests; metric smoke on a tiny fixture corpus; deptry ignore for transitive `openai` |
| W6 | `[notebook]` marimo mount + three example notebooks; docs rewrite (novice + expert paths); general-RAG demo corpus | Notebook-import smoke; docs gates (STE100/Vale) |

Parallel, credential-gated, external: **PyPI publish (issue #2)** is the
true novice door. Repo-side readiness exists; the publish step needs
credentials from the owner.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| NiceGUI is a big dependency for the core server | It never touches core: `[ui]` extra, lazy import, `ui.run_with` only when installed. |
| SSE/streaming edge cases break real frontends | Test against the actual OpenAI SDK streaming parser in W2, not a hand-rolled frame check. |
| OpenTelemetry semconv churn | Pin OpenInference constants; our span names are ours; attributes are additive. |
| DeepEval drags `openai` (offline-harmless) into the env | Documented in deptry config; the judge path never sends traffic anywhere but local Ollama. |
| UI scope creep (auth, multi-user, mobile) | Explicit non-goals for v1: single local user, localhost, no auth. revisit only after G1 is met. |
| Two docs truths (README vs new paths) | W6 collapses quickstart/getting-started into the two persona paths; old anchors redirect. |
| Chainlit-style maintenance surprise on NiceGUI | Swap cost is bounded by rule 4.2 (thin UI over tested view-models); Chainlit and Gradio both mount into FastAPI if ever needed. |

---

## 7. Explicitly deferred (parking lot)

- DuckDB portability: separate approved plan at
  `docs/research/storage-portability-program.md` (owner-ordered "later").
- Multi-user auth and remote exposure for the UI.
- Graph visualization page (AGE queries exist; a canvas is unproven need).
- Ragas knowledge-graph test-set generation as a second eval path.
- OpenAI-compatible rerank passthrough for external callers (`/v1/rerank`
  is already spoken by llama.cpp; exposing ours is small once W2 lands).

---

## 8. Evidence appendix (verified 2026-10-13)

Primary-source checks: PyPI JSON for every package version/license above;
Open WebUI license page (branding clause, 50-user rule, v0.6.6 cutoff);
Phoenix ELv2 (arize docs + repo LICENSE; `arize-phoenix` 20.23.0,
`arize-phoenix-evals` 3.9.2 both Elastic-2.0); Langfuse ClickHouse
acquisition announcement 2026-01-16 and 6-container compose; Jaeger v2.22.0
release assets (native Windows/macOS/Linux binaries, 2026-10-06); NiceGUI
`ui.run_with` docs; Chainlit `mount_chainlit` docs; Mesop archive event
2026-10-02; GPT4All dormancy (last release 2025-02-24). Weaker-evidence
items, flagged: Gradio exact minor (page summary), LobeChat conditions and
Jan relicense date (search summaries), promptfoo/OpenAI claim (unverified).

---

## 9. Stress-test amendments (2026-10-13, plan-approved)

The plan was stress-tested against every CI gate (workflow-by-workflow
audit) before execution. Five findings supersede the text above where
they conflict:

1. The HTTP app is plain Starlette, not FastAPI. NiceGUI's documented
   `ui.run_with` mount needs FastAPI. W3 converts `create_http_app()` to
   return `fastapi.FastAPI` (a Starlette subclass; routes, TestClient,
   and uvicorn unchanged) with fastapi as a core dependency.
2. `query` never generates answer text — the only retrieve-generate-cite
   pipeline today is transcripts-domain `notebook_ask`. W1 is therefore a
   new first-class `ask` operation (retrieval → generate → cite → verify)
   carrying the full four-surface parity tax, not an envelope refactor.
3. `/v1/*` is a protocol adapter outside the operation catalogue
   (research_http precedent) and touches zero parity gates. Pinned
   envelope key-sets of search/query/verify stay untouched.
4. Every dependency-adding wave trips the license lock check
   (uv.lock → licenses.lock.json). All dependency declarations batch
   into W0 with a user-approved one-time lock refresh; later waves never
   touch lock files. The lock walks only the base-dependency closure, so
   extras change only uv.lock; fastapi/opentelemetry-api (core) change
   both. fastapi/opentelemetry-api carry temporary DEP002 ignores until
   their import edges land in W3/W4.
5. Nothing streams anywhere; every Ollama call hardcodes stream=False.
   W2 ships complete-answer SSE (valid OpenAI frames emitted after
   generation completes); token streaming is a fast-follow touching only
   the LLM handler.

Wave order is now: W0 (deps + lock) → W1 (`ask` op) → W2 ∥ W3 →
W4 ∥ W5 → W6, with parallel implementation agents and serial rebase
merges. Gate mechanics that shaped the plan: the MCP contract snapshot
auto-discovers new `tools/*_tools.py` modules; new catalogue ops need
all four surfaces plus LEGS, VALID_TOOL_NAMES, and `poe gen` artifacts;
new config keys need get_default_config + both byte-equal YAMLs +
RUNTIME_CONFIG_KEYS; new docs under `docs/*.md` top level obey the
Vale 20-word sentence cap and FK grade 12 (docs/research is exempt);
xenon holds abs C / avg B and pyproject must never contain a `%`.

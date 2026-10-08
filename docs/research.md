# Research subsystem

Corpus-KB's research subsystem turns interview and meeting transcripts into a
coded, queryable, audited qualitative dataset. Start with
[getting-started.md](getting-started.md); this page is the command and config
reference.

## The pipeline at a glance

1. **Ingest** transcripts: `corpus-kb research ingest-transcript <file>` for
   one file, `corpus-kb research ingest <dir|glob> [--watch]` for dynamic
   ingestion with file-hash + text-hash dedup.
2. **Build the codebook**: `corpus-kb coding inductive` proposes themes
   (optional `inductive` extra); `corpus-kb codebook promote` is the human
   gate that mints each new codebook version.
3. **Code the corpus**: `corpus-kb coding run --codebook-version-id <uuid>`
   applies the deductive v2 engine (three-view scoring, calibrated
   thresholds, conformal routing).
4. **Review**: `corpus-kb review accept|override <assignment-id>` executes
   human decisions; overrides feed threshold refits and the G3 audit.
5. **Report**: `corpus-kb research report [--level novice|expert]` emits the
   ONE governance artifact (saturation, exhaustiveness, overlap, IRR, G3).
6. **Notebook**: `corpus-kb research ask "<question>" --project-id <uuid>`
   answers with exchange-level citations; `research evidence|uncoded|overlap`
   are the analyst views.
7. **Orchestrate**: `corpus-kb research cycle` chains the steps with
   in/on/out-of-loop postures and human-critical gates.

`corpus-kb research demo` runs steps 1-6 end-to-end on a bundled mini corpus
with narration - the fastest way to see the shapes of every artifact.

## Deployment note: embeddings

The research embedding boundary requires EXACTLY 1024 dims (native or
MRL-sliced from >=1024). The default `pgml` provider abstains when the pgml
extension is absent, and 768-dim models (nomic-embed-text) always abstain:
vectors stay NULL and retrieval degrades to lexical-only. Real research
deployments should set `embedding.provider: ollama` with a >=1024-dim model
(for example `qwen3-embedding:8b-q8_0`). `corpus-kb doctor` warns about this
shape.

## Config reference

Every `research.*` key below ships in the default config
(`get_default_config` / packaged `config.yaml`). This table is pinned by a
test that parses column 1 against the defaults, so new keys cannot land
undocumented.

| Key | Default | Effect |
|-----|---------|--------|
| `research.embedder.model_revision` | `"1024"` | Revision tag recorded on every research embedding row and cache entry. |
| `research.embedder.strategy` | `naive-prefix` | Research embedding strategy: the G1-promoted winner (`naive-prefix` or `latechunk`). |
| `research.embedder.dimensions` | `1024` | Promotion guard: research embedders must emit exactly 1024 dims; anything else halts promotion and abstains at run time. |
| `research.ingest.watch_interval_s` | `10` | Poll interval (seconds) for `research ingest --watch` drop-directory tailing. |
| `research.inductive.entropy_threshold` | `0.85` | Normalized soft-entropy gate above which a unit gets the LLM "existing code vs new code" meta-decision. |
| `research.inductive.recluster_every_batches` | `8` | Full re-cluster cadence during incremental inductive growth (v5 §9.5: every 5-10 batches). |
| `research.inductive.centroid_drift_threshold` | `0.15` | Mean cosine drift between centroid snapshots that triggers an early cluster refresh. |
| `research.inductive.tau_dup` | `0.85` | Promote-time duplicate gate: max prototype cosine vs existing codes above which a promotion blocks with a merge suggestion. |

## Optional extras

- `pip install -e ".[latechunk]"` - late-chunking embedder (G1 A/B loser on
  the pinned fixture; kept as the causal reference arm).
- `pip install -e ".[inductive]"` - UMAP/HDBSCAN clustering for the
  inductive engine.

Both are optional: `corpus-kb doctor` reports their status and the core
pipeline never requires them.

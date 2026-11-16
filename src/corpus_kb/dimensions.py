"""The shipped embedding widths (issue 237).

One leaf module owns the width numbers, so config, storage, rag,
handlers, projections, research, and _setup cannot drift apart. Each
constant documents its provenance here. Python SQL text interpolates
these names, so a width change lands in one place. The migration SQL
pins the same widths on disk (migrations/025_halfvec_2560.sql and its
migrations_duckdb mirror) and is the schema-side source of truth.
"""

from __future__ import annotations

# The statement width. chunks_vectors.embedding is halfvec(2560)
# (migration 025); the DuckDB mirror stores FLOAT[2560]. Equal to the
# native width of the default embedder (qwen3-embedding:4b; probe
# values live in rag/model_profiles.py). This is the config
# embedding.dimensions default (config.py) and the DuckDB FLOAT[k]
# twin width (storage/dialect.py, projections/embed_projection.py).
EMBEDDING_DIMENSIONS = 2560

# The research width. research_units, research_exchanges, and
# shadow_vectors store exactly this many dims: native, or an MRL
# front slice of a >=1024-dim embedder (migration 010's
# _slice_normalize rule). An embedder below 1024 dims abstains; it
# never pads. Also the width of the chunks vector_1024 matryoshka
# slice. Both consumers share one 1024-d family on purpose.
RESEARCH_EMBEDDING_DIMENSIONS = 1024

# The nomic width. nomic-embed-text native output (probe-verified in
# rag/model_profiles.py). The legacy fallback default for the DuckDB
# rag store (storage/duckdb_rag.py) and the LlamaIndex backend
# (storage/llamaindex_backend.py). It sits below the research width,
# so a nomic embedder abstains from research writes.
NOMIC_EMBEDDING_DIMENSIONS = 768

# Named embedding presets ("levers"): one key in embedding.preset picks
# the model + vector width + batch_size tradeoff. Keys not set in the
# user's config file are filled from the preset; explicit keys win.
# fast     = qwen3-embedding:0.6b, 1024d (MRL front-slice native width):
#            quarter the vector bytes of the default, clear quality
#            step over nomic-embed.
# balanced = qwen3-embedding:4b, 2560d: the shipped default embedder.
# quality  = qwen3-embedding:8b-q8_0, 4096d (MTEB top; biggest memory
#            and per-chunk bytes).
# All three are MRL-trained (front-slice + L2 renorm is sound), so the
# research arm's 1024-d slice stays valid for every preset.
EMBEDDING_PRESETS: dict[str, dict[str, object]] = {
    "fast": {
        "model": "qwen3-embedding:0.6b",
        "dimensions": 1024,
        "batch_size": 64,
    },
    "balanced": {
        "model": "qwen3-embedding:4b",
        "dimensions": EMBEDDING_DIMENSIONS,
        "batch_size": 32,
    },
    "quality": {
        "model": "qwen3-embedding:8b-q8_0",
        "dimensions": 4096,
        "batch_size": 128,
    },
}

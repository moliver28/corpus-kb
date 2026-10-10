"""U31 benchmark: candidate embedders on the repo's own fixture corpus.

Compares the current default (nomic-embed-text) against the Qwen3-Embedding
models available on the local Ollama, scored with the U45 metric family
(recall@k, MRR) plus embed latency (p50/p95), against the AUTHORED fixture
labels below — labels are human-written fixture truth, latency/recall
numbers are local-machine measurements, not CI truths.

Embedder/dimension changes are NEVER in-place: each candidate is a versioned
EmbeddingProfile, and switching profiles means a NEW index generation plus a
rebuild (see the profile contract here; the cutover decision is out of
scope for this script — it informs, the admin decides).

When Ollama is unreachable the degraded-mode zero vectors are DETECTED and
the profile is reported ``not_evaluable`` instead of a fake recall of 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_BASE_URL = "http://localhost:11434"
LABEL = "local-machine measurement against authored fixture labels, not CI truth"
FIXTURE_VERSION = 1


@dataclass(frozen=True)
class EmbeddingProfile:
    """One versioned embedder configuration (U31 contract).

    Switching profiles = NEW index generation + rebuild, never in-place;
    ``version`` bumps when model OR dimensions change under the same name.
    """

    name: str
    model: str
    dimensions: int
    version: int = FIXTURE_VERSION
    instruction: str | None = None


CURRENT_PROFILES: tuple[EmbeddingProfile, ...] = (
    EmbeddingProfile(name="default", model="nomic-embed-text", dimensions=768),
    EmbeddingProfile(
        name="qwen3-embedding-4b",
        model="qwen3-embedding:4b",
        dimensions=2560,
        instruction=(
            "Instruct: Given a qualitative research query, retrieve relevant "
            "interview exchanges\nQuery: "
        ),
    ),
    EmbeddingProfile(
        name="qwen3-embedding-8b",
        model="qwen3-embedding:8b-q8_0",
        dimensions=4096,
        instruction=(
            "Instruct: Given a qualitative research query, retrieve relevant "
            "interview exchanges\nQuery: "
        ),
    ),
)

# Authored fixture corpus: doc id -> text (labels authored by hand; the same
# texts double as the determinism fixture for the structure tests).
FIXTURE_DOCS: dict[str, str] = {
    "d1": "The billing team shipped the new invoice reconciliation pipeline on Friday.",
    "d2": "Housing policy Committee minutes: rent stabilization vote delayed again.",
    "d3": "Participants described difficulties accessing mental health services locally.",
    "d4": "The migration sprint plan allocates two sprints for the database cutover.",
    "d5": "A facilitator asked the group how transport costs affect school attendance.",
    "d6": "Marketing wants a teaser campaign before the public announcement next month.",
    "d7": "Renters reported mold complaints that the landlord ignored for months.",
    "d8": "The reimbursement process for travel expenses was simplified this quarter.",
}

# query -> doc ids that genuinely answer it (the relevance labels).
FIXTURE_QUERIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("How do participants feel about renting and housing costs?", ("d2", "d7")),
    ("What did people say about billing and invoices?", ("d1", "d8")),
    ("Barriers to health care access in the community", ("d3",)),
    ("Timeline for the migration work", ("d4",)),
    ("Transport and travel to school", ("d5",)),
    ("Publicity plans before launch", ("d6",)),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--models",
        default=",".join(p.model for p in CURRENT_PROFILES),
        help="comma-separated Ollama model names to benchmark",
    )
    parser.add_argument("--k", type=int, default=2)
    return parser.parse_args(argv)


def profile_for_model(model: str) -> EmbeddingProfile:
    for profile in CURRENT_PROFILES:
        if profile.model == model:
            return profile
    raise ValueError(f"no EmbeddingProfile for model {model!r}")


def doctor_mismatch_note(
    profile: EmbeddingProfile,
    configured_model: str,
    configured_dimensions: int,
) -> str | None:
    """Doctor extension point: mismatch text when config diverges from a profile.

    Returns None when the installed config matches this profile exactly;
    otherwise a human-readable note. The doctor wiring itself lives in
    _setup (not this script's scope) — this is the note it should print.
    """
    if profile.model == configured_model and profile.dimensions == configured_dimensions:
        return None
    return (
        f"embedding profile mismatch: config is {configured_model}@{configured_dimensions}d "
        f"but profile {profile.name!r} is {profile.model}@{profile.dimensions}d — switching "
        f"requires a NEW versioned index generation + rebuild, never an in-place change"
    )


def rank_ids(matrix: np.ndarray, query: np.ndarray, k: int) -> list[int]:
    """Exact cosine ranking over unit vectors; returns row indices."""
    sims = matrix @ query
    return [int(i) for i in np.argsort(-sims)[:k]]


def is_zero_vector(vector: np.ndarray) -> bool:
    return bool(np.all(vector == 0.0))


def _query_text(text: str, profile: EmbeddingProfile) -> str:
    return profile.instruction + text if profile.instruction else text


def evaluate_profile(
    embed_batch: object,
    docs: dict[str, str],
    queries: tuple[tuple[str, tuple[str, ...]], ...],
    profile: EmbeddingProfile,
    k: int,
) -> dict[str, object]:
    """Embed docs+queries with ``embed_batch(texts) -> list[list[float]]`` and score.

    Latency samples cover the embed calls; recall/MRR use exact numpy
    cosine ranking against the authored labels.
    """
    doc_ids = sorted(docs)
    start = time.perf_counter()
    doc_matrix_raw = embed_batch([docs[doc_id] for doc_id in doc_ids])
    doc_ms = (time.perf_counter() - start) * 1000.0
    doc_matrix = np.asarray(doc_matrix_raw, dtype=np.float32)
    norms = np.linalg.norm(doc_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    doc_matrix = doc_matrix / norms

    if any(is_zero_vector(row) for row in np.asarray(doc_matrix_raw, dtype=np.float32)):
        return {
            "profile": profile.name,
            "model": profile.model,
            "dimensions": profile.dimensions,
            "status": "not_evaluable",
            "reason": "degraded-mode zero vectors (embedder unreachable); no fake metrics",
        }

    recall_samples: list[float] = []
    mrr_samples: list[float] = []
    query_ms: list[float] = []
    for query, relevant in queries:
        start = time.perf_counter()
        query_vec = embed_batch([_query_text(query, profile)])[0]
        query_ms.append((time.perf_counter() - start) * 1000.0)
        vec = np.asarray(query_vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        vec = vec / norm if norm > 0 else vec
        ranked = [doc_ids[i] for i in rank_ids(doc_matrix, vec, k)]
        relevant_set = set(relevant)
        hits = sum(1 for doc_id in ranked if doc_id in relevant_set)
        recall_samples.append(hits / len(relevant_set))
        mrr = 0.0
        for pos, doc_id in enumerate(ranked, start=1):
            if doc_id in relevant_set:
                mrr = 1.0 / pos
                break
        mrr_samples.append(mrr)

    samples = [doc_ms, *query_ms]
    arr = np.asarray(samples, dtype=np.float64)
    return {
        "profile": profile.name,
        "model": profile.model,
        "dimensions": profile.dimensions,
        "status": "ok",
        "recall_at_k": float(np.mean(recall_samples)),
        "mrr": float(np.mean(mrr_samples)),
        "p50_embed_ms": float(np.percentile(arr, 50)),
        "p95_embed_ms": float(np.percentile(arr, 95)),
        "n_queries": len(queries),
        "k": k,
    }


def run_benchmark(models: list[str], base_url: str, k: int) -> dict[str, object]:
    """Benchmark the requested models on the fixture corpus."""
    from corpus_kb.rag.embedder import OllamaEmbedder

    profiles: list[dict[str, object]] = []
    for model in models:
        profile = profile_for_model(model)
        embedder = OllamaEmbedder(
            {"embedding": {"model": model, "base_url": base_url, "dimensions": profile.dimensions}}
        )
        profiles.append(
            evaluate_profile(embedder.embed_batch, FIXTURE_DOCS, FIXTURE_QUERIES, profile, k)
        )
    return {
        "status": "ok" if profiles else "insufficient_data",
        "label": LABEL,
        "fixture_version": FIXTURE_VERSION,
        "profiles": profiles,
        "note": (
            "embedder/dimension changes require a NEW versioned embedding profile and a "
            "rebuild; the cutover decision is out of scope for this script"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_benchmark(
        [m.strip() for m in args.models.split(",") if m.strip()], args.base_url, args.k
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

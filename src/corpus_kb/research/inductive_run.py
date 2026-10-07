"""Inductive run pipeline (todo 15, v5 §9) — the abductive pilot leg.

Sequencing (v5 §7): this is step 1/3 — an inductive pilot pass that drafts
proposed codes for human promotion; the deductive run (deductive_run.py)
then scales the promoted codebook; the residual (gray-zone, HDBSCAN noise)
routes back to review.

One ``run_inductive`` pass:
  1. summarize units to atomic observations (temp 0, question-context aware,
     prompt/model logged; prior observations reused across runs);
  2. embed summaries (embedding_cache-backed 1024-d);
  3. PILOT: UMAP+HDBSCAN full cluster (determinism pins in the run manifest);
  4. INCREMENTAL GROWTH: remaining units in batches -> k=2-4 nearest
     centroids + Student-t soft-assignment entropy (Tier-0 signal rows);
     only high-entropy units earn the LLM existing-vs-new meta-decision;
  5. cadence: centroid batch refresh; early refresh when the cosine drift
     between snapshots exceeds the threshold; full re-cluster every
     ``recluster_every_batches`` batches;
  6. FINAL: full re-cluster -> proposed_code log (centroid snapshots =
     PROVENANCE ONLY, summary space, r7) + noise bucket -> manual-review
     queue (never discarded) + ISR per batch in CodingRun checkpoints with
     DBCV tracked against the previous run's baseline (>20% drop flags
     re-cluster review).

Extras contract (r8): the optional umap/hdbscan stack is reached ONLY via
``coding._inductive_types``; without the extra the run returns
``{"status": "unavailable", ...}`` and the deductive path (which never
imports this module) is unaffected.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from corpus_kb.coding.inductive_cluster import (
    NOISE_LABEL,
    centroid_drift,
    centroids_in_original_space,
    clamp_k,
    cluster_embeddings,
    dbcv_drop_flag,
    is_high_entropy,
    nearest_centroids,
    noise_labels_original_space,
    run_manifest,
    soft_assignment_entropy,
)
from corpus_kb.coding.inductive_summaries import SummaryChatClient, meta_decide, summarize_unit
from corpus_kb.coding.saturation import isr
from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.research.inductive_store import (
    find_observation,
    latest_baseline_dbcv,
    load_codable_pairs,
    queue_noise,
    record_signal,
    upsert_observation,
    upsert_proposal,
)
from corpus_kb.research.promote_code import TAU_DUP_DEFAULT
from corpus_kb.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class SummaryEmbedder(Protocol):
    """Embedder surface the inductive run uses (ResearchEmbedder satisfies it)."""

    model: str
    model_revision: str

    async def embed_cached(self, tenant_id: UUID, text: str) -> list[float] | None: ...


PILOT_UNITS = 60
INCREMENTAL_BATCH_SIZE = 25
ENTROPY_THRESHOLD_DEFAULT = 0.85
RECLUSTER_EVERY_BATCHES_DEFAULT = 8
CENTROID_DRIFT_THRESHOLD_DEFAULT = 0.15
MAX_META_DECISIONS_PER_RUN = 50

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "with",
        "you",
        "your",
    ]
)


def inductive_config(cfg: dict[str, object] | None) -> dict[str, object]:
    """research.inductive block with engine defaults filled in."""
    research = (cfg or {}).get("research") or {}
    block = (research if isinstance(research, dict) else {}).get("inductive") or {}
    values = block if isinstance(block, dict) else {}
    return {
        "entropy_threshold": float(values.get("entropy_threshold", ENTROPY_THRESHOLD_DEFAULT)),
        "recluster_every_batches": int(
            values.get("recluster_every_batches", RECLUSTER_EVERY_BATCHES_DEFAULT)
        ),
        "centroid_drift_threshold": float(
            values.get("centroid_drift_threshold", CENTROID_DRIFT_THRESHOLD_DEFAULT)
        ),
        "tau_dup": float(values.get("tau_dup", TAU_DUP_DEFAULT)),
    }


def suggested_label(summaries: list[str]) -> str:
    """Deterministic top-content-terms label (ties break alphabetically)."""
    counts: Counter[str] = Counter()
    for text in summaries:
        for token in text.lower().split():
            word = "".join(ch for ch in token if ch.isalnum())
            if len(word) > 2 and word not in _STOPWORDS:
                counts[word] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [w for w, _ in ranked[:3]]
    return "-".join(top) if top else "cluster"


async def run_inductive(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    project_id: UUID | None = None,
    llm: SummaryChatClient | None = None,
    embedder: SummaryEmbedder | None = None,
    cfg: dict[str, object] | None = None,
) -> dict[str, object]:
    """One inductive pass; returns the summary the CLI/MCP surfaces print."""
    from corpus_kb.coding._inductive_types import InductiveDependencyError

    handler = ResearchHandler(pool)
    settings = inductive_config(cfg)
    run_info = handler.start_coding_run(
        tenant_id,
        params={"pipeline": "inductive", **dict(settings)},
    )
    run_id = UUID(str(run_info["run_id"]))
    try:
        return await _execute(pool, handler, tenant_id, run_id, project_id, llm, embedder, settings)
    except InductiveDependencyError as exc:
        handler.stop_coding_run(tenant_id, run_id)
        return {
            "status": "unavailable",
            "run_id": str(run_id),
            "reason": str(exc),
            "install": 'pip install -e ".[inductive]"',
        }
    except Exception:
        handler.stop_coding_run(tenant_id, run_id)
        raise


async def _execute(
    pool: asyncpg.Pool,
    handler: ResearchHandler,
    tenant_id: UUID,
    run_id: UUID,
    project_id: UUID | None,
    llm: SummaryChatClient | None,
    embedder: SummaryEmbedder | None,
    settings: dict[str, object],
) -> dict[str, object]:
    baseline = await latest_baseline_dbcv(pool, tenant_id, run_id)
    async with tenant_connection(pool, tenant_id) as conn:
        pairs = await load_codable_pairs(conn, tenant_id, project_id)
        code_names = await _existing_code_names(conn, tenant_id)
        observations = await _observe(conn, tenant_id, run_id, pairs, llm, embedder)

    if len(observations) < 2:
        handler.stop_coding_run(tenant_id, run_id)
        return {
            "status": "success",
            "run_id": str(run_id),
            "n_units": len(observations),
            "note": "fewer than 2 embedded observations; nothing to cluster",
        }

    vectors = [obs["embedding"] for obs in observations]
    pilot_n = min(PILOT_UNITS, len(vectors))
    manifest = run_manifest(pilot_n)
    handler.checkpoint_coding_run(tenant_id, run_id, {"kind": "manifest", "manifest": manifest})

    pilot = cluster_embeddings(vectors[:pilot_n], **_pins(manifest))
    dbcv_baseline = baseline if baseline is not None else pilot.relative_validity
    centroids = centroids_in_original_space(vectors[:pilot_n], pilot.labels)
    batch_stats = await _grow(
        pool,
        handler,
        tenant_id,
        run_id,
        observations,
        pilot.labels,
        centroids,
        code_names,
        llm,
        settings,
        _pins(manifest),
    )

    final = cluster_embeddings(vectors, **_pins(manifest))
    final_centroids = centroids_in_original_space(vectors, final.labels)
    drift = centroid_drift(centroids, final_centroids)
    dropped = dbcv_drop_flag(dbcv_baseline, final.relative_validity)
    original_noise = noise_labels_original_space(vectors, pilot.min_cluster_size)
    async with tenant_connection(pool, tenant_id) as conn:
        for label, members in _cluster_members(final.labels).items():
            await upsert_proposal(
                conn,
                tenant_id,
                cluster_id=f"{run_id}:cluster:{label}",
                run_id=run_id,
                batch_no=batch_stats["batches"],
                suggested_label=suggested_label([observations[i]["summary"] for i in members]),
                centroid=final_centroids.get(label),
                member_unit_ids=[observations[i]["unit_id"] for i in members],
                dbcv_relative_validity=final.relative_validity,
            )
        noise_units = 0
        for i, label in enumerate(final.labels):
            if label == NOISE_LABEL or original_noise[i] == NOISE_LABEL:
                await queue_noise(
                    conn,
                    tenant_id,
                    observations[i]["unit_id"],
                    run_id,
                    probability=final.probabilities[i],
                    entropy=None,
                )
                noise_units += 1

    summary = {
        "status": "success",
        "run_id": str(run_id),
        "n_units": len(observations),
        "pilot_units": pilot_n,
        "n_clusters": len(_cluster_members(final.labels)),
        "noise_units": noise_units,
        "dbcv_relative_validity": final.relative_validity,
        "dbcv_baseline": dbcv_baseline,
        "dbcv_drop_flag": dropped,
        "centroid_drift": drift,
        **batch_stats,
    }
    handler.checkpoint_coding_run(
        tenant_id,
        run_id,
        {
            "kind": "final",
            "dbcv_relative_validity": final.relative_validity,
            "dbcv_baseline": dbcv_baseline,
            "dbcv_drop_flag": dropped,
            "n_clusters": summary["n_clusters"],
            "noise_units": noise_units,
            "centroid_drift": drift,
            **batch_stats,
        },
    )
    handler.stop_coding_run(tenant_id, run_id)
    return summary


def _pins(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "n_components": int(str(manifest["n_components"])),
        "random_state": int(str(manifest["umap_random_state"])),
        "transform_seed": int(str(manifest["umap_transform_seed"])),
    }


def _cluster_members(labels: list[int]) -> dict[int, list[int]]:
    members: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        if label != NOISE_LABEL:
            members.setdefault(label, []).append(i)
    return members


async def _observe(
    conn: asyncpg.Connection,
    tenant_id: UUID,
    run_id: UUID,
    pairs: list[dict[str, Any]],
    llm: SummaryChatClient | None,
    embedder: SummaryEmbedder | None,
) -> list[dict[str, Any]]:
    """Summarize + embed every codable unit (reusing prior observations)."""
    observations: list[dict[str, Any]] = []
    for pair in pairs:
        unit_id = pair["unit_id"]
        stored = await find_observation(conn, tenant_id, unit_id)
        embedding = stored["embedding"] if stored else None
        if stored is None and llm is not None:
            summary = await summarize_unit(llm, unit_id, pair["question"], pair["answer"])
            stored = {
                "summary": summary.summary,
                "question_dependent": summary.question_dependent,
                "prompt_id": summary.prompt_id,
                "model": summary.model,
                "temperature": summary.temperature,
                "parse_fallback": summary.parse_fallback,
                "embedding": None,
            }
        if stored is None:
            stored = {
                "summary": pair["answer"][:400],
                "question_dependent": False,
                "prompt_id": "fallback.raw_answer",
                "model": "none",
                "temperature": 0.0,
                "parse_fallback": True,
                "embedding": None,
            }
        if embedding is None and embedder is not None:
            embedding = await embedder.embed_cached(tenant_id, stored["summary"])
        await upsert_observation(
            conn,
            tenant_id,
            unit_id,
            run_id,
            summary=stored["summary"],
            question_dependent=stored["question_dependent"],
            prompt_id=stored["prompt_id"],
            model=stored["model"],
            temperature=stored["temperature"],
            parse_fallback=stored["parse_fallback"],
            embedding=embedding,
            embed_model=str(getattr(embedder, "model", "unknown")) if embedder else "none",
            model_revision=str(getattr(embedder, "model_revision", "")) if embedder else "",
        )
        if embedding:
            observations.append(
                {"unit_id": unit_id, "summary": stored["summary"], "embedding": embedding}
            )
    return observations


async def _grow(
    pool: asyncpg.Pool,
    handler: ResearchHandler,
    tenant_id: UUID,
    run_id: UUID,
    observations: list[dict[str, Any]],
    pilot_labels: list[int],
    centroids: dict[int, list[float]],
    code_names: list[str],
    llm: SummaryChatClient | None,
    settings: dict[str, object],
    pins: dict[str, object],
) -> dict[str, object]:
    """Incremental growth: batches vs centroids, ISR per batch, meta-decisions.

    Cadence (v5 §9.5): after each batch the centroids REFRESH incrementally
    (members = pilot + every unit assigned to its nearest label, means taken
    in the ORIGINAL summary-embedding space); the cosine drift between the
    previous and refreshed snapshots triggers an EARLY full re-cluster, and a
    full re-cluster also fires every ``recluster_every_batches`` batches.
    """
    threshold = float(str(settings["entropy_threshold"]))
    recluster_every = int(str(settings["recluster_every_batches"]))
    drift_threshold = float(str(settings["centroid_drift_threshold"]))
    remaining = observations[len(pilot_labels) :]
    batches = 0
    high_entropy_total = 0
    meta_new_total = 0
    early_reclusters = 0
    current_centroids = centroids
    max_drift: float | None = None
    assigned: list[int] = list(pilot_labels)
    for start in range(0, len(remaining), INCREMENTAL_BATCH_SIZE):
        batch = remaining[start : start + INCREMENTAL_BATCH_SIZE]
        batches += 1
        labels_touched: set[int] = set()
        high_entropy = 0
        meta_new = 0
        async with tenant_connection(pool, tenant_id) as conn:
            for obs in batch:
                nearest = nearest_centroids(obs["embedding"], current_centroids, k=3)
                assignments, entropy = soft_assignment_entropy(obs["embedding"], current_centroids)
                k = clamp_k(3, len(current_centroids))
                labels_touched.update(nearest)
                if nearest:
                    assigned.append(nearest[0])
                else:
                    assigned.append(NOISE_LABEL)
                if is_high_entropy(entropy, k, threshold) and llm is not None:
                    high_entropy += 1
                    question, answer = await _pair_texts(conn, tenant_id, obs["unit_id"])
                    decision = await meta_decide(llm, obs["unit_id"], question, answer, code_names)
                    if decision.decision == "new":
                        meta_new += 1
                    extra: dict[str, object] = {
                        "meta_decision": decision.decision,
                        "meta_prompt_id": decision.prompt_id,
                        "assignments": assignments,
                    }
                else:
                    extra = {"assignments": assignments}
                await record_signal(
                    conn,
                    tenant_id,
                    obs["unit_id"],
                    run_id,
                    soft_cluster_entropy=entropy,
                    n_clusters=k,
                    extra=extra,
                )
        handler.checkpoint_coding_run(
            tenant_id,
            run_id,
            {
                "kind": "batch",
                "batch_no": batches,
                "isr": isr(len(labels_touched), len(batch)),
                "n_units": len(batch),
                "high_entropy": high_entropy,
                "meta_new": meta_new,
            },
        )
        high_entropy_total += high_entropy
        meta_new_total += meta_new

        previous = current_centroids
        current_centroids = centroids_in_original_space(
            [o["embedding"] for o in observations], assigned
        )
        drift = centroid_drift(previous, current_centroids)
        if drift is not None:
            max_drift = drift if max_drift is None else max(max_drift, drift)
        due = batches % recluster_every == 0
        early = drift is not None and drift > drift_threshold
        if due or early:
            if early:
                early_reclusters += 1
            refreshed = cluster_embeddings([o["embedding"] for o in observations], **pins)
            assigned = refreshed.labels
            current_centroids = centroids_in_original_space(
                [o["embedding"] for o in observations], assigned
            )
    return {
        "batches": batches,
        "high_entropy": high_entropy_total,
        "meta_new": meta_new_total,
        "max_batch_drift": max_drift,
        "early_reclusters": early_reclusters,
    }


async def _pair_texts(conn: asyncpg.Connection, tenant_id: UUID, unit_id: int) -> tuple[str, str]:
    row = await conn.fetchrow(
        """
        SELECT u.text AS answer, q.text AS question
        FROM research_units u
        JOIN research_exchanges x
          ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
        LEFT JOIN LATERAL (
            SELECT text FROM research_units
            WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
              AND role_in_exchange = 'question'
            ORDER BY seq LIMIT 1
        ) q ON TRUE
        WHERE u.tenant_id = $1 AND u.unit_id = $2
        """,
        str(tenant_id),
        unit_id,
    )
    if row is None:
        return "", ""
    return str(row["question"] or ""), str(row["answer"] or "")


async def _existing_code_names(conn: asyncpg.Connection, tenant_id: UUID) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT name FROM code_registry
        WHERE tenant_id = $1 AND name <> ''
        ORDER BY name LIMIT 100
        """,
        str(tenant_id),
    )
    return [str(row["name"]) for row in rows]

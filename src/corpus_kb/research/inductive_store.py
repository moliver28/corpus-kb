"""Read-model state for the inductive engine (todo 15).

Engine-owned operational tables (migration 017): research_observations,
research_proposed_codes, research_noise_queue. Same ownership class as the
content-addressed turn store — written by the inductive run under the
tenant GUC, excluded from the event-rebuild drop-set. Every write is
idempotent (ON CONFLICT) so re-runs are safe.

Declared DuckDB divergences: vector/json casts (``::JSON``, fixed
``FLOAT[1024]``), list-membership reads (``list_contains``), and the JSON
``?``-operator probe that becomes a newest-first Python key scan (the
engine allows one writer, so the read-modify semantics hold).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.projections.research._embed import _parse_pg_vector
from corpus_kb.research.conn import Store, tenant_scope

_VECTOR = f"::FLOAT[{RESEARCH_EMBEDDING_DIMENSIONS}]"


def _vec_cast(duck: bool, idx: str) -> str:
    """The vector parameter cast: fixed FLOAT[1024] on DuckDB, ::vector on PG."""
    return f"{idx}{_VECTOR}" if duck else f"{idx}::vector"


def _json_cast(duck: bool, idx: str) -> str:
    return f"{idx}::JSON" if duck else f"{idx}::jsonb"


def _ids_member(duck: bool, idx: str, column: str) -> str:
    if duck:
        return f"list_contains({idx}::BIGINT[], {column})"
    return f"{column} = ANY({idx}::bigint[])"


async def load_codable_pairs(
    conn: Any, tenant_id: UUID, project_id: UUID | None, duck: bool = False
) -> list[dict[str, Any]]:
    """Codable answer units with their moderator question text (stable order)."""
    project_test = (
        "($2::VARCHAR IS NULL OR u.project_id = $2::VARCHAR)"
        if duck
        else "($2::uuid IS NULL OR u.project_id = $2::uuid)"
    )
    rows = await conn.fetch(
        f"""
        SELECT u.unit_id, u.text AS answer, q.text AS question, x.stance
        FROM research_units u
        JOIN research_exchanges x
          ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
        LEFT JOIN LATERAL (
            SELECT text FROM research_units
            WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
              AND role_in_exchange IN ('question', 'main_question')
            ORDER BY seq LIMIT 1
        ) q ON TRUE
        WHERE u.tenant_id = $1 AND u.is_codable AND u.role_in_exchange = 'answer'
          AND {project_test}
        ORDER BY u.unit_id
        """,
        str(tenant_id),
        str(project_id) if project_id else None,
    )
    return [
        {
            "unit_id": int(row["unit_id"]),
            "answer": str(row["answer"] or ""),
            "question": str(row["question"] or ""),
            "stance": row["stance"],
        }
        for row in rows
    ]


async def find_observation(conn: Any, tenant_id: UUID, unit_id: int) -> dict[str, Any] | None:
    """Latest existing observation for a unit (temp-0 reuse across runs)."""
    row = await conn.fetchrow(
        """
        SELECT summary, question_dependent, prompt_id, model, temperature,
               parse_fallback, embedding
        FROM research_observations
        WHERE tenant_id = $1 AND unit_id = $2
        ORDER BY observation_id DESC LIMIT 1
        """,
        str(tenant_id),
        unit_id,
    )
    if row is None:
        return None
    return {
        "summary": str(row["summary"]),
        "question_dependent": bool(row["question_dependent"]),
        "prompt_id": str(row["prompt_id"]),
        "model": str(row["model"]),
        "temperature": float(row["temperature"] or 0.0),
        "parse_fallback": bool(row["parse_fallback"]),
        "embedding": _parse_pg_vector(row["embedding"]) if row["embedding"] else None,
    }


async def upsert_observation(
    conn: Any,
    tenant_id: UUID,
    unit_id: int,
    run_id: UUID,
    *,
    summary: str,
    question_dependent: bool,
    prompt_id: str,
    model: str,
    temperature: float,
    parse_fallback: bool,
    embedding: list[float] | None,
    embed_model: str,
    model_revision: str,
    duck: bool = False,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO research_observations
        (tenant_id, unit_id, run_id, summary, question_dependent, prompt_id,
         model, temperature, parse_fallback, embedding, embedding_model,
         model_revision, dimensions)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, {_vec_cast(duck, "$10")}, $11, $12, $13)
        ON CONFLICT (tenant_id, unit_id, run_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            question_dependent = EXCLUDED.question_dependent,
            parse_fallback = EXCLUDED.parse_fallback,
            embedding = EXCLUDED.embedding
        """,
        str(tenant_id),
        unit_id,
        str(run_id),
        summary,
        question_dependent,
        prompt_id,
        model,
        temperature,
        parse_fallback,
        _vector_literal(embedding),
        embed_model,
        model_revision,
        len(embedding) if embedding else None,
    )


def _vector_literal(vec: list[float] | None) -> str | None:
    if not vec:
        return None
    return "[" + ",".join(f"{x:.9g}" for x in vec) + "]"


async def record_signal(
    conn: Any,
    tenant_id: UUID,
    unit_id: int,
    run_id: UUID,
    *,
    soft_cluster_entropy: float,
    n_clusters: int,
    extra: dict[str, object],
    duck: bool = False,
) -> None:
    """Tier-0 inductive signal row (todo-16's consumption surface)."""
    await conn.execute(
        f"""
        INSERT INTO research_signals
        (tenant_id, unit_id, run_id, tier, soft_cluster_entropy, n_clusters, extra)
        VALUES ($1, $2, $3, 0, $4, $5, {_json_cast(duck, "$6")})
        ON CONFLICT (tenant_id, unit_id, run_id, tier) DO UPDATE SET
            soft_cluster_entropy = EXCLUDED.soft_cluster_entropy,
            n_clusters = EXCLUDED.n_clusters,
            extra = EXCLUDED.extra
        """,
        str(tenant_id),
        unit_id,
        str(run_id),
        soft_cluster_entropy,
        n_clusters,
        json.dumps(extra),
    )


async def queue_noise(
    conn: Any,
    tenant_id: UUID,
    unit_id: int,
    run_id: UUID,
    *,
    probability: float | None,
    entropy: float | None,
) -> None:
    """HDBSCAN noise bucket -> manual-review queue (never discarded)."""
    await conn.execute(
        """
        INSERT INTO research_noise_queue
        (tenant_id, unit_id, run_id, cluster_label, probability, entropy)
        VALUES ($1, $2, $3, -1, $4, $5)
        ON CONFLICT (tenant_id, unit_id, run_id) DO NOTHING
        """,
        str(tenant_id),
        unit_id,
        str(run_id),
        probability,
        entropy,
    )


async def upsert_proposal(
    conn: Any,
    tenant_id: UUID,
    *,
    cluster_id: str,
    run_id: UUID,
    batch_no: int,
    suggested_label: str,
    centroid: list[float] | None,
    member_unit_ids: list[int],
    dbcv_relative_validity: float | None,
    duck: bool = False,
) -> None:
    members = "$8::BIGINT[]" if duck else "$8::bigint[]"
    await conn.execute(
        f"""
        INSERT INTO research_proposed_codes
        (tenant_id, cluster_id, run_id, batch_no, suggested_label, centroid,
         centroid_snapshot, member_unit_ids, n_members, dbcv_relative_validity)
        VALUES ($1, $2, $3, $4, $5, {_vec_cast(duck, "$6")},
                {_json_cast(duck, "$7")}, {members}, $9, $10)
        ON CONFLICT (tenant_id, cluster_id, run_id) DO UPDATE SET
            centroid = EXCLUDED.centroid,
            centroid_snapshot = EXCLUDED.centroid_snapshot,
            member_unit_ids = EXCLUDED.member_unit_ids,
            n_members = EXCLUDED.n_members,
            dbcv_relative_validity = EXCLUDED.dbcv_relative_validity
        """,
        str(tenant_id),
        cluster_id,
        str(run_id),
        batch_no,
        suggested_label,
        _vector_literal(centroid),
        json.dumps([float(x) for x in centroid or []]),
        member_unit_ids,
        len(member_unit_ids),
        dbcv_relative_validity,
    )


async def load_unit_views_by_ids(
    conn: Any, tenant_id: UUID, unit_ids: list[int], duck: bool = False
) -> dict[int, tuple[list[float], list[float], list[float]]]:
    """A/QA/Q view vectors for specific units (promotion re-derivation input).

    Mirrors run_inputs' three-view SQL (answer unit + exchange QA + question
    unit) filtered to the given unit ids; units with incomplete views are
    omitted from the result.
    """
    if not unit_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT u.unit_id, u.embedding AS a_vec, x.embedding AS qa_vec,
               q.embedding AS q_vec
        FROM research_units u
        JOIN research_exchanges x
          ON x.exchange_id = u.exchange_id AND x.tenant_id = u.tenant_id
            LEFT JOIN LATERAL (
                SELECT embedding FROM research_units
                WHERE exchange_id = u.exchange_id AND tenant_id = u.tenant_id
                  AND role_in_exchange IN ('question', 'main_question')
                  AND embedding IS NOT NULL
                ORDER BY seq LIMIT 1
            ) q ON TRUE
            WHERE u.tenant_id = $1 AND {_ids_member(duck, "$2", "u.unit_id")}
              AND u.embedding IS NOT NULL AND x.embedding IS NOT NULL
        """,
        str(tenant_id),
        unit_ids,
    )
    result: dict[int, tuple[list[float], list[float], list[float]]] = {}
    for row in rows:
        a_vec = _parse_pg_vector(row["a_vec"]) if row["a_vec"] else None
        qa_vec = _parse_pg_vector(row["qa_vec"]) if row["qa_vec"] else None
        q_vec = _parse_pg_vector(row["q_vec"]) if row["q_vec"] else None
        if a_vec and qa_vec and q_vec:
            result[int(row["unit_id"])] = (a_vec, qa_vec, q_vec)
    return result


async def latest_baseline_dbcv(
    store: Store, tenant_id: UUID, run_id: UUID, duck: bool = False
) -> float | None:
    """Most recent DBCV from earlier runs' checkpoints (drop tracking).

    Postgres keeps its byte-identical ``?``-operator probe; DuckDB has no
    JSON ``?``, so the rows scan newest-first in Python (single-writer
    engine, same first-match semantics).
    """
    probe = (
        "AND checkpoint IS NOT NULL"
        if duck
        else "AND checkpoint IS NOT NULL AND checkpoint ? 'dbcv_relative_validity'"
    )
    limit = "" if duck else "LIMIT 1"
    async with tenant_scope(store, tenant_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT checkpoint FROM research_runs
            WHERE tenant_id = $1 AND run_id <> $2
              {probe}
            ORDER BY started_at DESC {limit}
            """,
            str(tenant_id),
            str(run_id),
        )
    for row in rows:
        checkpoint = row["checkpoint"]
        if isinstance(checkpoint, str):
            checkpoint = json.loads(checkpoint)
        value = (checkpoint or {}).get("dbcv_relative_validity")
        if value is not None:
            return float(value)
    return None

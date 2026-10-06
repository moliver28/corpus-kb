"""Wire saturation.py's ISR/stop rule to the tenant's live coding state.

isr()/run_stop() are pure over caller-supplied counts; this module derives
those counts from final_codes/chunk_codes so an operator (or an automated
gate) can ask "have we saturated yet" for a tenant without hand-computing
anything, mirroring the DB-facing wiring split already used by
floors_calibration.py and keyword_governance_report.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.saturation import isr, run_stop
from corpus_kb.storage.tenant_conn import tenant_connection


async def compute_saturation(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    batch_id: str | None = None,
    threshold: float = 0.05,
    min_samples: int = 50,
) -> dict[str, Any]:
    """Report ISR and (when `batch_id` is given) the stop-rule verdict.

    unique_codes: distinct code_id with an accepted final_codes row.
    total_applications: total chunk_codes rows (every coding decision made),
      the denominator isr() expects for "coded applications".

    With `batch_id`, new_codes is the count of accepted codes whose EARLIEST
    acceptance falls within that batch's own chunk_codes time window (i.e.
    codes genuinely first discovered by that run, not just re-confirmed by
    it), and base_unique is the unique-code count BEFORE that run
    (unique_codes minus new_codes) -- the denominator run_stop's docstring
    calls "discovered so far". Without a batch_id there is no "last run" to
    compare against, so `should_stop` is reported as None rather than guessed.
    """
    async with tenant_connection(pool, tenant_id) as conn:
        unique_codes = await conn.fetchval(
            "SELECT COUNT(DISTINCT code_id) FROM final_codes "
            "WHERE tenant_id=$1 AND status='accepted'",
            tenant_id,
        )
        total_applications = await conn.fetchval(
            "SELECT COUNT(*) FROM chunk_codes WHERE tenant_id=$1", tenant_id
        )
        unique_codes = unique_codes or 0
        total_applications = total_applications or 0

        new_codes: int | None = None
        base_unique: int | None = None
        should_stop: bool | None = None

        if batch_id is not None:
            window = await conn.fetchrow(
                "SELECT MIN(created_at) AS start, MAX(created_at) AS stop "
                "FROM chunk_codes WHERE tenant_id=$1 AND batch_id=$2",
                tenant_id,
                batch_id,
            )
            if window and window["start"] is not None:
                new_codes = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT code_id, MIN(decided_at) AS first_accept
                        FROM final_codes
                        WHERE tenant_id=$1 AND status='accepted'
                        GROUP BY code_id
                    ) accepted
                    WHERE first_accept BETWEEN $2 AND $3
                    """,
                    tenant_id,
                    window["start"],
                    window["stop"],
                )
                new_codes = new_codes or 0
                base_unique = max(unique_codes - new_codes, 0)
                should_stop = run_stop(
                    new_codes=new_codes,
                    base_unique=base_unique,
                    threshold=threshold,
                    min_samples=min_samples,
                )
            else:
                new_codes = 0
                base_unique = unique_codes

    return {
        "status": "success",
        "unique_codes": unique_codes,
        "total_applications": total_applications,
        "isr": isr(unique_codes, total_applications),
        "batch_id": batch_id,
        "new_codes": new_codes,
        "base_unique": base_unique,
        "should_stop": should_stop,
    }

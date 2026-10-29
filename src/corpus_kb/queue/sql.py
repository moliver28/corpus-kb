"""The ingest_queue SQL (textbook-ingestion program).

One module owns every queue statement: the Wave 1 ops (enqueue probe,
insert, get, list, cancel) and the Wave 2C worker ops (claim, stale
reclaim, heartbeat, release, finish, fail). All SQL is parameterized,
tenant-scoped by the session GUC the store sets, and deterministically
ordered (``ORDER BY queued_at, id``) so harnesses can snapshot reads.

The claim uses ``FOR UPDATE SKIP LOCKED``: many workers can drain one
Postgres queue; each claim takes exactly one row. DuckDB has no such
clause and allows one writer, so the worker refuses the duckdb backend
instead of racing (see ``queue.worker``).
"""

from __future__ import annotations

_ROW_COLUMNS = (
    "id::text, tenant_id::text, path, title, project_id::text, profile, "
    "strategy_config, status, attempts, max_attempts, last_error, "
    "run_id::text, content_hash, claimed_by, heartbeat_at, queued_at, "
    "started_at, finished_at, updated_at, "
    "pages_done, pages_total, chunks"
)

# The partial unique index backs this probe (one ACTIVE row per hash); the
# INSERT's ON CONFLICT DO NOTHING closes the race between probe and insert.
FIND_ACTIVE_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND content_hash = $2
  AND status IN ('pending', 'running')
ORDER BY queued_at, id
LIMIT 1
"""

INSERT_SQL = f"""
INSERT INTO ingest_queue (
    tenant_id, path, title, project_id, profile, strategy_config,
    status, attempts, max_attempts, content_hash
) VALUES ($1, $2, $3, $4::uuid, $5, $6::jsonb, 'pending', 0, $7, $8)
ON CONFLICT DO NOTHING
RETURNING {_ROW_COLUMNS}
"""

GET_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND id = $2::uuid
"""

LIST_ACTIVE_SQL = f"""
SELECT {_ROW_COLUMNS}
FROM ingest_queue
WHERE tenant_id = $1 AND status IN ('pending', 'running')
ORDER BY queued_at, id
LIMIT $2
"""

CANCEL_SQL = f"""
UPDATE ingest_queue
SET status = 'cancelled', finished_at = now(), updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status IN ('pending', 'running')
RETURNING {_ROW_COLUMNS}
"""

# --- Wave 2C worker ops -----------------------------------------------------

#: Claim the OLDEST pending row for one worker identity. The subquery's
#: ``FOR UPDATE SKIP LOCKED`` keeps concurrent workers on distinct rows;
#: the guard re-checks status so a cancelled row is never claimed.
#: ``$3`` is the pass watermark (the worker's clock): rows first claimed
#: at or after it wait for a later pass, so one drain pass gives each row
#: one attempt instead of a tight retry loop. An app clock slightly ahead
#: of the database only relaxes that to same-pass retries.
CLAIM_SQL = f"""
UPDATE ingest_queue q
SET status = 'running', claimed_by = $2, heartbeat_at = now(),
    started_at = COALESCE(q.started_at, now()), updated_at = now()
WHERE q.id = (
    SELECT c.id FROM ingest_queue c
    WHERE c.tenant_id = $1 AND c.status = 'pending'
      AND (c.started_at IS NULL OR c.started_at < $3::timestamptz)
    ORDER BY c.queued_at, c.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING {_ROW_COLUMNS}
"""

#: A running row whose heartbeat went quiet goes back to pending. attempts
#: stays (a stale lease is a crash, not a counted failure). The note lands
#: in last_error so the status payload explains the reset.
RECLAIM_STALE_SQL = f"""
UPDATE ingest_queue
SET status = 'pending', claimed_by = NULL, heartbeat_at = NULL,
    last_error = 'The worker lease ran out. The row is pending again.',
    updated_at = now()
WHERE tenant_id = $1 AND status = 'running'
  AND heartbeat_at < now() - make_interval(secs => $2)
RETURNING {_ROW_COLUMNS}
"""

#: One live heartbeat per book. No row back means the claim was lost
#: (cancel or reclaim) and the worker must stop the book.
HEARTBEAT_SQL = """
UPDATE ingest_queue
SET heartbeat_at = now(), updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status = 'running' AND claimed_by = $3
RETURNING id::text
"""

#: Put one claim back untouched (stop before the pipeline started).
#: attempts stays: a released claim is not a failure.
RELEASE_SQL = f"""
UPDATE ingest_queue
SET status = 'pending', claimed_by = NULL, heartbeat_at = NULL, updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status = 'running' AND claimed_by = $3
RETURNING {_ROW_COLUMNS}
"""

#: Claim-time metadata fill (Wave 3): the best-effort pypdf page count,
#: written right after the claim so queue-status shows the total while
#: the book runs. A NULL probe never writes; the statement matches only
#: the worker's own live claim.
PAGES_TOTAL_SQL = f"""
UPDATE ingest_queue
SET pages_total = $3, updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status = 'running' AND claimed_by = $4
RETURNING {_ROW_COLUMNS}
"""

#: Success: done, the pipeline's run id, and the progress counters.
#: ``pages_total`` COALESCEs so the claim-time probe fill survives; the
#: pipeline reports no page counts itself. last_error carries the stage
#: errors of a "partial" run and clears on a clean one.
FINISH_SQL = f"""
UPDATE ingest_queue
SET status = 'done', run_id = COALESCE($3::uuid, run_id),
    pages_done = $4, pages_total = COALESCE($5, pages_total), chunks = $6,
    last_error = $7, claimed_by = NULL, heartbeat_at = NULL,
    finished_at = now(), updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status = 'running' AND claimed_by = $8
RETURNING {_ROW_COLUMNS}
"""

#: Failure: attempts goes up once. The row retries (pending) while
#: attempts stays under max_attempts, else it lands on failed.
FAIL_SQL = f"""
UPDATE ingest_queue
SET attempts = attempts + 1,
    status = CASE WHEN attempts + 1 < max_attempts THEN 'pending' ELSE 'failed' END,
    last_error = $3,
    claimed_by = NULL, heartbeat_at = NULL,
    finished_at = CASE WHEN attempts + 1 >= max_attempts THEN now() ELSE finished_at END,
    updated_at = now()
WHERE tenant_id = $1 AND id = $2::uuid AND status = 'running' AND claimed_by = $4
RETURNING {_ROW_COLUMNS}
"""

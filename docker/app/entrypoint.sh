#!/bin/sh
# Corpus-KB app container entrypoint.
#
# Order: wait for Postgres, apply migrations, then serve HTTP. The DSN and
# every other setting come from the environment; no secrets live in this
# file. POSIX sh; the python:3.11-slim base ships both sh and python.
set -e

APP_PORT="${CORPUS_KB_PORT:-8010}"
APP_HOST="${CORPUS_KB_HOST:-0.0.0.0}"

if [ -z "${CORPUS_KB_DATABASE_URL:-}" ]; then
    echo "corpus-kb entrypoint: CORPUS_KB_DATABASE_URL is not set. Set the DSN and retry." >&2
    exit 3
fi

# Wait for Postgres (60 s cap, 2 s between tries). Exit 3 is the taxonomy's
# database-unavailable code, so compose `--wait` and supervisors see an
# honest failure, not a silent hang.
python - <<'PYEOF'
import asyncio
import os
import sys
import time

import asyncpg

dsn = os.environ["CORPUS_KB_DATABASE_URL"]
deadline = time.monotonic() + 60.0
last_error = "no attempt made"
while True:
    try:
        asyncio.run(asyncpg.connect(dsn, timeout=5))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - any connect failure means "not up yet"
        last_error = str(exc)
        if time.monotonic() >= deadline:
            print(
                f"corpus-kb entrypoint: Postgres not reachable after 60 s: {last_error}",
                file=sys.stderr,
            )
            sys.exit(3)
        time.sleep(2.0)
PYEOF

echo "corpus-kb entrypoint: Postgres is up. Applying the schema bootstrap."
# TWO idempotent steps: the ONE shared bootstrap (storage/schema.sql +
# non-gated migrations, the quickstart path), then corpus-kb migrate for
# the extension-gated remainder (004/005 create the AGE graph; the image
# ships the extensions, so they apply here). Early attempts can land
# while the image's first-boot init finishes — retry with a deadline;
# give up with the database-unavailable exit code.
bootstrap() {
    python -c "import asyncio, os; from corpus_kb._setup.schema_apply import apply_schema; r = asyncio.run(apply_schema(os.environ['CORPUS_KB_DATABASE_URL'])); print('schema bootstrap: applied %d, skipped %d' % (len(r.applied), len(r.skipped)))" || return 1
    corpus-kb migrate || return 1
}
ATTEMPT=1
MAX_ATTEMPTS=24
while true; do
    if bootstrap; then
        echo "corpus-kb entrypoint: schema bootstrap complete (attempt ${ATTEMPT})."
        break
    fi
    if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        echo "corpus-kb entrypoint: schema bootstrap failed after ${MAX_ATTEMPTS} attempts." >&2
        exit 3
    fi
    echo "corpus-kb entrypoint: bootstrap attempt ${ATTEMPT} failed. Retrying in 5 s."
    ATTEMPT=$((ATTEMPT + 1))
    sleep 5
done

echo "corpus-kb entrypoint: starting the server on ${APP_HOST}:${APP_PORT}."
exec corpus-kb start --transport http --port "${APP_PORT}" --host "${APP_HOST}"

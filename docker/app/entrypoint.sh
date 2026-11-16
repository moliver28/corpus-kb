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

echo "corpus-kb entrypoint: Postgres is up. Applying migrations."
corpus-kb migrate

echo "corpus-kb entrypoint: starting the server on ${APP_HOST}:${APP_PORT}."
exec corpus-kb start --transport http --port "${APP_PORT}" --host "${APP_HOST}"

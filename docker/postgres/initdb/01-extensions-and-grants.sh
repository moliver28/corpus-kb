#!/bin/bash
# First-boot init for the Corpus-KB Postgres image. The stock postgres
# entrypoint runs this once per fresh data dir, as the bootstrap superuser.
# It fixes the two known live-install pains up front:
#   1. pgvector is untrusted (no `trusted` in vector.control), and age/pgml
#      must be preloaded: pre-create every extension the corpus_kb
#      migrations expect, as superuser, before migrations run.
#   2. Hardened schemas may revoke PUBLIC USAGE: grant USAGE ON SCHEMA
#      public to the app role explicitly.
# Idempotent: IF NOT EXISTS guards; the entrypoint skips this dir when the
# data volume already exists, so a compose re-up never re-runs nor fails.
set -euo pipefail

db="${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"
role="${POSTGRES_USER:-postgres}"

psql -v ON_ERROR_STOP=1 -v app_role="$role" --username "$role" --dbname "$db" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS pgml;
GRANT USAGE ON SCHEMA public TO :"app_role";
SQL

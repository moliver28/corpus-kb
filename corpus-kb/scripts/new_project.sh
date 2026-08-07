#!/usr/bin/env bash
# Create and migrate a per-project Corpus-KB database.
#
# Usage:  scripts/new_project.sh <project-slug>
# Example: scripts/new_project.sh opsaudit-churn
#
# Produces a database named corpus_kb_<slug> owned by corpus_user, with all
# migrations applied (including FORCE row-level security). Prints the
# CORPUS_KB_DATABASE_URL to use when running the server against that project.
#
# Prerequisites: Postgres running locally, a superuser role that can createdb
# and CREATE EXTENSION, and the corpus_user role (password corpus_pass).
set -euo pipefail

SLUG="${1:-}"
if [[ -z "$SLUG" ]]; then
  echo "usage: $0 <project-slug>   (e.g. opsaudit-churn)" >&2
  exit 2
fi

# Normalize slug to a legal, collision-resistant db name: lowercase, non-alnum -> _
SAFE="$(printf '%s' "$SLUG" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_' | sed 's/_\{2,\}/_/g; s/^_//; s/_$//')"
DB="corpus_kb_${SAFE}"
URL="postgresql://corpus_user:corpus_pass@localhost:5432/${DB}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Creating project database: ${DB}"
createdb "${DB}" -O corpus_user
psql -d "${DB}" -q -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS age;'
psql -d "${DB}" -q -c 'GRANT ALL PRIVILEGES ON SCHEMA public TO corpus_user; GRANT USAGE ON SCHEMA ag_catalog TO corpus_user;'

echo "Applying migrations..."
CORPUS_KB_DATABASE_URL="${URL}" python "${SCRIPT_DIR}/migrate.py"

psql -d "${DB}" -q -c 'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_user; GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_user;'

echo
echo "Project database ready: ${DB}"
echo "Run the server against it with:"
echo "  export CORPUS_KB_DATABASE_URL=${URL}"
echo "  python -m src.server_wiring --transport http --port 8010"

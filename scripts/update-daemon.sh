#!/usr/bin/env bash
# Fail-closed updater for the corpus-kb daemon (issue #452 state machine).
# Abort on a dirty worktree; no-op when HEAD == origin/master; health-check
# before and after; stop the daemon while migrations run; roll back to the
# previous commit on any failure. Log: ~/.corpus-kb/update.log
# Open gates from issue #452 (owner machine): stats snapshot compare,
# DIMENSION-CHANGE guard, ingest-in-flight lock, external alerting.
set -euo pipefail

REPO="${CORPUSKB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BIN="$REPO/.venv/bin/corpus-kb"
PY="$REPO/.venv/bin/python"
LABEL="com.corpuskb.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${CORPUSKB_PORT:-8010}"
LOG="$HOME/.corpus-kb/update.log"
UID_N="$(id -u)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
die() { log "$1"; exit "${2:-1}"; }
healthy() { curl -fsS -m 4 "http://127.0.0.1:$PORT/api/doctor" | grep -q '"status"'; }

deps() {
    if command -v uv >/dev/null 2>&1; then
        uv sync --frozen --project "$REPO"
    else
        "$PY" -m pip install -e "$REPO" --quiet
    fi
}

# Safe only because the dirty check below passed: a clean worktree loses
# nothing, and reflog still holds the newer sha.
rollback() {
    git -C "$REPO" reset --hard "$FROM" || true
    deps || true
    bash "$REPO/scripts/install-daemon.sh" || true
    log "ROLLBACK restored $FROM"
}

mkdir -p "$(dirname "$LOG")"
cd "$REPO"
git fetch origin master --quiet

[[ -z "$(git status --porcelain)" ]] || die "DIRTY: uncommitted changes; update aborted" 3

HEAD_SHA="$(git rev-parse HEAD)"
MASTER_SHA="$(git rev-parse origin/master)"
if [[ "$HEAD_SHA" == "$MASTER_SHA" ]]; then
    log "UNBEATEN $HEAD_SHA"
    exit 0
fi
FROM="$HEAD_SHA"

healthy || die "DAEMON-DOWN: unhealthy before the update; nothing changed" 4

# Stop the daemon so the dependency and migration steps own the database file.
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true

if ! git merge --ff-only origin/master; then
    bash "$REPO/scripts/install-daemon.sh" || true
    die "FF-ONLY-FAILED: master diverged; daemon restored, nothing updated" 5
fi
deps || { rollback; die "DEPS-FAILED: dependency sync failed" 6; }
"$BIN" migrate >>"$LOG" 2>&1 || { rollback; die "MIGRATE-FAILED: migrations failed" 7; }
bash "$REPO/scripts/install-daemon.sh" || { rollback; die "START-FAILED: daemon did not come up" 8; }
healthy || { rollback; die "HEALTH-FAILED: post-update health check failed" 9; }

log "UPDATED $FROM -> $MASTER_SHA"

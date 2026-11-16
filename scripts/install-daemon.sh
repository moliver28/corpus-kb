#!/usr/bin/env bash
# Install the corpus-kb HTTP daemon as a KeepAlive launchd LaunchAgent.
# The daemon owns the database file (single writer on DuckDB); every other
# consumer connects over HTTP. Idempotent: safe to re-run any time.
# Note: launchctl bootstrap fails inside some agent shells; run this from a
# normal terminal. Health check: GET /api/doctor must answer.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$REPO/.venv/bin/corpus-kb"
LABEL="com.corpuskb.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/.corpus-kb/daemon.log"
PORT="${CORPUSKB_PORT:-8010}"

[[ -x "$BIN" ]] || { echo "corpus-kb not found at $BIN (run pip install -e .)" >&2; exit 1; }
mkdir -p "$HOME/.corpus-kb" "$(dirname "$PLIST")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BIN</string>
        <string>start</string>
        <string>--transport</string><string>http</string>
        <string>--port</string><string>$PORT</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>KeepAlive</key><true/>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

UID_N="$(id -u)"
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$PLIST"
launchctl enable "gui/$UID_N/$LABEL"
sleep 6

if curl -fsS -m 4 "http://127.0.0.1:$PORT/api/doctor" | grep -q '"status"'; then
    echo "OK: daemon serving on :$PORT (launchd: $LABEL, log: $LOG)"
else
    echo "WARN: daemon not answering yet; check $LOG" >&2
    exit 2
fi

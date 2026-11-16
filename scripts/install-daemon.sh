#!/usr/bin/env bash
# Install the corpus-kb SSE daemon as a KeepAlive LaunchAgent.
# Run OUTSIDE the Hermes gateway agent shell (the gateway blocks launchctl
# bootstrap); an SSH self-loop is a working path for unattended runs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.corpuskb.server.plist"
LOG="$HOME/.corpus-kb/daemon.log"
PORT="${CORPUSKB_PORT:-8013}"

[[ -x "$PY" ]] || { echo "corpus-kb venv not found at $REPO/.venv" >&2; exit 1; }
mkdir -p "$HOME/.corpus-kb"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.corpuskb.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string><string>-m</string><string>server</string>
        <string>--transport</string><string>sse</string>
        <string>--port</string><string>$PORT</string>
        <string>--config</string><string>$REPO/config.yaml</string>
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
launchctl bootout "gui/$UID_N/com.corpuskb.server" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$PLIST"
launchctl enable "gui/$UID_N/com.corpuskb.server"
sleep 6

if curl -s -m 4 "http://127.0.0.1:$PORT/sse" | head -c 1 | grep -q event; then
    echo "OK: daemon serving on :$PORT (launchd: com.corpuskb.server)"
else
    echo "WARN: daemon not answering yet; check $LOG" >&2
    exit 2
fi

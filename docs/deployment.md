# Long-running deployment: one daemon, HTTP MCP

Long-running consumption (sessions across profiles, cron jobs) fails on the
stdio design because every connection spawns its own server and DuckDB's
single-writer lock turns a second spawn into `Connection closed`. Fix: run
ONE server as a daemon and let all consumers connect over HTTP.

## Layout

- Daemon: `.venv/bin/python -m server --transport sse --port 8013 --config config.yaml`
  from the repo root.
- Supervisor: LaunchAgent `com.corpuskb.server` (KeepAlive + RunAtLoad), user
  log at `~/.corpus-kb/daemon.log`.
- MCP: `mcp_servers.corpus-kb = {url: "http://127.0.0.1:8013/sse"}` in every
  profile's `config.yaml`. No per-session spawn, so the lock race disappears:
  only the daemon ever holds `corpus.db`.

## Install

```bash
bash scripts/install-daemon.sh
```

Idempotent: writes `~/Library/LaunchAgents/com.corpuskb.server.plist`,
bootstraps, then health-checks. `launchctl bootstrap` is forbidden inside
the Hermes gateway agent shell, so run installs from a normal terminal
(an SSH self-loop works for unattended runs).

## Operations

| Task | Command |
| --- | --- |
| Health | `curl -m 4 http://127.0.0.1:8013/sse` (expect `event: endpoint`) |
| Restart | `launchctl kickstart gui/501/com.corpuskb.server` |
| Kill-restart after a lock conflict | `launchctl kickstart -k gui/501/com.corpuskb.server` |
| Logs | `tail -50 ~/.corpus-kb/daemon.log` |

`Connection closed` on corpus-kb almost always means a stray process holds
the DuckDB lock: `ps aux | grep corpus-kb/.venv`, kill it, then
`launchctl kickstart gui/501/com.corpuskb.server` and retest.

## Notes

- DuckDB single-writer still applies: only the daemon touches `corpus.db`;
  ad-hoc scripts should go through the daemon's MCP/HTTP surface, or stop
  the daemon first.
- The plist pins the current v1 entry point; if the server module or
  transport changes, update the plist and `kickstart`.

# Deployment: one daemon, HTTP consumers

This page is the runbook for a long-running install. One server runs as a daemon. Every other consumer connects over HTTP.

## Why one daemon

The DuckDB backend allows one writer per database file. A second process that opens the same file fails on a lock error. That is why per-connection spawning breaks on DuckDB. One daemon holds the file for its whole life. All consumers then share one healthy server.

On Postgres, many processes can share the database. The daemon pattern still helps there. It gives you one port, one log, and one lifecycle.

## Layout

- **Daemon**: `corpus-kb start --transport http --port 8010` from the repo root. It serves the REST API, the browser UI, the OpenAI-compatible adapter, and the JSON-RPC socket.
- **Supervisor**: launchd agent `com.corpuskb.server` with KeepAlive and RunAtLoad.
- **Log**: `~/.corpus-kb/daemon.log` holds stdout and stderr.
- **Install**: `bash scripts/install-daemon.sh`. The script is idempotent. It writes the plist, bootstraps the agent, and health-checks.

## Editor agents on DuckDB

MCP for editors runs over stdio. A stdio session spawns its own process. On DuckDB that process fights the daemon for the file lock. Pick one owner for the file:

- Stop the daemon before a heavy stdio session: `launchctl bootout gui/$(id -u)/com.corpuskb.server`.
- Or move the study to the Postgres backend, which allows both processes.

## Operations

| Task | Command |
|------|---------|
| Health | `curl -m 4 http://localhost:8010/api/doctor` |
| Logs | `tail -50 ~/.corpus-kb/daemon.log` |
| Restart | `launchctl kickstart -k gui/$(id -u)/com.corpuskb.server` |
| Stop | `launchctl bootout gui/$(id -u)/com.corpuskb.server` |
| Start after a stop | `bash scripts/install-daemon.sh` |
| Ad-hoc CLI work on DuckDB | Stop the daemon, run the command, start the daemon again |

The health route prints `"status": "success"` when the server answers. A lock error in the log almost always means a stray process holds the file. Find it with `ps aux | grep corpus-kb`, kill it, then restart the agent.

## Updates

`scripts/update-daemon.sh` moves a daemon host to the latest master. It is fail-closed:

1. Abort on a dirty worktree.
2. Exit `UNBEATEN` when HEAD already matches `origin/master`.
3. Health-check before the update.
4. Stop the daemon, pull with `--ff-only`, sync dependencies, apply migrations.
5. Reinstall the agent and health-check again.
6. Roll back to the previous commit on any failure, and log `ROLLBACK`.

Schedule it daily with one launchd line or a cron entry. Its log lands in `~/.corpus-kb/update.log`.

## Notes

- `launchctl bootstrap` fails inside some agent shells. Run the installer from a normal terminal.
- Keep exactly ONE checkout feeding the daemon. No second server, no stale branch copy.
- The plist pins absolute paths. If you move the checkout, run the installer again.

## Where to go next

- [INSTALL.md](INSTALL.md) — first-time setup
- [API.md](API.md) — HTTP routes for consumers
- [STORAGE-BACKENDS.md](STORAGE-BACKENDS.md) — Postgres vs DuckDB
- [OPERATIONS.md](OPERATIONS.md) — ports, exit codes, vocabularies

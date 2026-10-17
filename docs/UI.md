# Browser UI

The browser UI is a small web front end for first-time users. It ships
four pages: Overview, Add, Ask, and Library. The pages ride the running
HTTP server. There is no new daemon and no new port.

## Install

NiceGUI lives behind the optional `[ui]` extra:

```bash
pip install 'corpus-kb[ui]'
```

CI never installs this extra. The server imports it lazily, so the core
package stays small without it.

## Start

```bash
corpus-kb start
```

The server mounts the pages and opens your browser at
`http://localhost:8010`. The auto-open waits about one second, then
calls your browser. Set `CORPUS_KB_UI_OPEN=0` to keep the tab closed.

The flag `--ui` forces the pages on. The flag `--no-ui` forces them
off. With neither flag, the `ui.enabled` config key decides.

## The four pages

- **Overview** shows the doctor's health checks in plain language. Each
  row names one part: configuration, database, models. A failed row
  shows the fix.
- **Add** ingests your files. Drag files onto the page, or paste a page
  address (URL). A progress bar tracks each file. Each row reports
  chunks, connections, or the error with its fix. The demo button runs
  the narrated demo pipeline and shows its steps.
- **Ask** answers one question from your documents. Each sentence cites
  its sources. A badge states the verdict: `verified`, `partial`, or
  `not verified`. The badge never claims a pass on an abstention.
- **Library** lists your documents and the discovered topics. Each read
  uses the same tools the API uses.

## Config

```yaml
ui:
  enabled: true
```

The `ui.enabled` key must be a boolean. A bad value refuses to load.

## Honest degradation

Without the `[ui]` extra, the server logs one line and runs on:

```text
[ui] extra not installed — browser UI disabled. pip install 'corpus-kb[ui]'
```

The MCP tools, the HTTP API, and the CLI keep working. A broken UI
mount never stops the server either. The log line names the reason.

## How it mounts

`corpus-kb start` builds the HTTP app with FastAPI. The UI attaches to
that app with NiceGUI's `ui.run_with`. Your pages, the REST API, and
the socket all share one process and one port. The page logic lives in
`src/corpus_kb/ui/view_models.py`; it calls the same tool layer the
MCP and HTTP surfaces call. The results can differ only when your data
changes between calls.

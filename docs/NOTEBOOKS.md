# Notebooks

Corpus-KB serves [marimo](https://marimo.io) notebooks from the running
HTTP server. A notebook is a Python file; cells run in one shared state.
The console mounts the `notebooks/` folder at `/notebooks` on the same
port as the API. There is no new daemon and no new port.

## Install

marimo lives behind the optional `[notebook]` extra:

```bash
pip install 'corpus-kb[notebook]'
```

CI never installs this extra. The server imports marimo lazily, so the
core package stays small without it.

## Start

```bash
corpus-kb start
```

With `notebooks.enabled: true` (the default), the server mounts
`/notebooks`. Open `http://localhost:8010/notebooks` and pick a notebook.

Missing extra or missing folder? The server logs one line and runs on.
The notebooks console is a convenience; it never blocks startup. Set
`notebooks.enabled: false` to skip the mount. `notebooks.directory`
names the served folder; it resolves against the working directory.

Known wart: notebooks reload on server restart. Edit a notebook, then
restart `corpus-kb start` to see the change.

## The example notebooks

Three notebooks ship in `notebooks/`. Each one imports `corpus_kb`
modules at cell level, so they run against your live database:

| Notebook | What it shows |
|----------|---------------|
| `explore_topics.py` | Lists built topics with sizes and keywords from the topics tools |
| `retrieval_ablation.py` | Runs one query through the FTS, vector, and RRF arms; shows the rank differences |
| `eval_report.py` | Loads a report from `corpus-kb eval run --report` and charts the metrics |

`eval_report.py` needs no database: point it at a saved report file. The
other two need a running database and, for the vector arm, the
configured embedding model.

To add your own notebook, drop a `.py` marimo file into the configured
folder. Restart the server once, and it appears at `/notebooks`.

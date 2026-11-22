# Corpus-KB documentation

This directory is the full documentation set. This page is the entry map: pick your goal, follow the path, and read only what you need.

## Start here

| You want to... | Path |
|----------------|------|
| Install and run | [INSTALL.md](INSTALL.md) → [UI.md](UI.md) → try the sample corpus (`corpus-kb demo-kb`) |
| Ingest your first files | [getting-started.md](getting-started.md) → [INGESTION.md](INGESTION.md) |
| Ask questions and search | [getting-started.md](getting-started.md) → [FEATURES.md](FEATURES.md) → [API.md](API.md) |
| Develop or extend | [DEVELOPMENT.md](DEVELOPMENT.md) → [INGESTION.md](INGESTION.md#ingestion-strategies) → [VALIDATION.md](VALIDATION.md) |
| Operate and maintain | [OPERATIONS.md](OPERATIONS.md) → [ADMIN.md](ADMIN.md) → [deployment.md](deployment.md) |

New to qualitative coding? Run `corpus-kb research demo` first, then read [getting-started.md](getting-started.md).

## Every page

| Page | Covers | For |
|------|--------|-----|
| [INSTALL.md](INSTALL.md) | Every install path: pipx fast path, Postgres, DuckDB, Docker | Everyone |
| [getting-started.md](getting-started.md) | First-run walkthrough: ingest, codebook, coding, report, ask | Novices |
| [FEATURES.md](FEATURES.md) | Feature tour plus the MCP tool reference table | Everyone |
| [UI.md](UI.md) | The browser app at `/app/` | Everyone |
| [FAQ.md](FAQ.md) | Quick answers to common questions | Everyone |
| [INGESTION.md](INGESTION.md) | Pipeline stages, strategies, and the extension guide | Builders |
| [ANALYSIS.md](ANALYSIS.md) | The offline analysis layer: `analyze-run`, topics, method selector | Analysts |
| [ANALYSIS-METHODS.md](ANALYSIS-METHODS.md) | The generated register of 33 analysis methods | Analysts |
| [NOTEBOOKS.md](NOTEBOOKS.md) | The marimo notebooks console | Analysts |
| [VALIDATION.md](VALIDATION.md) | Author a corpus profile, measure it, apply the winner | Researchers |
| [research.md](research.md) | Research subsystem: commands, config keys, cycle, releases | Researchers |
| [understanding-your-report.md](understanding-your-report.md) | Every governance-report field in plain language | Novices |
| [EVALUATION.md](EVALUATION.md) | Answer-quality evaluation runs and reports | Researchers |
| [API.md](API.md) | HTTP routes with curl examples | Integrators |
| [PLUGIN.md](PLUGIN.md) | The corpus-rag plugin for Claude Code and ZCode | Harness users |
| [OPERATIONS.md](OPERATIONS.md) | Exit codes, ports, vocabularies, the error taxonomy | Operators |
| [ADMIN.md](ADMIN.md) | Config keys, env vars, schema widths, backups | Operators |
| [deployment.md](deployment.md) | The daemon runbook and the updater | Operators |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Spans, `trace_id`, and what to watch | Operators |
| [ROLLBACK_DRILL.md](ROLLBACK_DRILL.md) | The practiced backup-and-restore drill | Operators |
| [STORAGE-BACKENDS.md](STORAGE-BACKENDS.md) | Postgres vs DuckDB, and how to switch | Operators |
| [SECURITY-TRIAGE.md](SECURITY-TRIAGE.md) | How to triage a security report | Maintainers |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Repo structure, quality gates, how to contribute | Developers |

## Directories

| Directory | What it is |
|-----------|------------|
| [research/](research/README.md) | Internal program records: plans, as-built reports, reference math. Index: [research/README.md](research/README.md). |
| [superpowers/](superpowers/) | Dated plans and specs from the build process. Internal records. They live here on purpose; every other page is user-facing. |
| [demo-kb/](demo-kb/) | Sample library the code reads. `corpus-kb demo-kb` ingests these five files. |
| [demo-corpus/](demo-corpus/) | Sample transcripts the code reads. `corpus-kb research demo` runs on them. |

## Word rules

Use one term per concept. The product is **Corpus-KB**. The package is **corpus-kb**. The data store is the **corpus**. Call the database **Postgres** in prose.

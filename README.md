# Corpus-KB

Local RAG system for AI code editors. Ingest your codebase. Ask questions. Get answers. No cloud.

**All documentation, setup instructions, and code are in [`corpus-kb/`](corpus-kb/).**

See [corpus-kb/README.md](corpus-kb/README.md) for full documentation.

## Quick Start

```bash
cd corpus-kb
pip install -e ".[postgres,dev]"
python -m src.server_wiring --transport http --port 8010
```

Requires PostgreSQL 17 with pgvector + Apache AGE. See [corpus-kb/docs/W0_POSTGRES_SETUP.md](corpus-kb/docs/W0_POSTGRES_SETUP.md).

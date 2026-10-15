# FAQ

Common questions about Corpus-KB.

---

## Do I need PostgreSQL?

Yes. The current release requires PostgreSQL 17 with pgvector and Apache AGE. Earlier prototypes used LanceDB, DuckDB, and SQLite, but the production storage layer is now Postgres.

## Do I need a GPU?

Mostly. `qwen3-embedding:4b` (the shipped default) runs on CPU, just slower than the old 768d nomic model. It produces 2560-dimensional embeddings. A GPU helps but is not required.

## Can I use OpenAI embeddings instead of Ollama?

Not out of the box. Corpus-KB currently calls Ollama's local embedding API. You could add a new embedder implementation that calls OpenAI, but that would require a code change.

## Can I run without Ollama?

Yes. Ingest continues when Ollama is unreachable, but the affected chunks are stored with zero vectors and flagged `degraded`. Search leaves degraded chunks out of vector results by default (`retrieval.exclude_degraded: true`). It reports what it excluded in the `degraded_excluded` field of the response, and still runs the full-text arm. Once Ollama is back, `corpus-kb ingest-reembed` re-embeds the flagged chunks and clears the flag.

## Can I teach it a new file format?

Yes — ingestion is pluggable at three levels. Map a source type to an existing strategy in `config.yaml` (no code). Register a strategy in-process with `register_strategy()`. Ship one as an entry-point plugin from your own package. See [Ingestion strategies](INGESTION-STRATEGIES.md).

## How is this different from a cloud vector database like Pinecone?

Corpus-KB is 100% local. Your code never leaves your machine. It also gives you three query patterns (vector search, SQL, and graph traversal) rather than just vector retrieval.

## How is this different from ripgrep?

Ripgrep finds exact string matches. Corpus-KB finds conceptually related code even when the keywords differ. It also understands structure: functions, classes, headings, and entity relationships.

## What is event sourcing?

Event sourcing means the event store is the source of truth. Every change is recorded as an immutable event. Projections read those events and build the Postgres read models. This gives you an audit trail, the ability to replay history, and a clean separation between writes and reads.

## How do I version my data?

Because every change is an event, you can list versions, tag important points, and check out or restore earlier states. Projections can be rebuilt from any point in the event stream.

## What languages does tree-sitter support?

Python, JavaScript, TypeScript, JSX, TSX, Rust, Go, Java, and C are supported. So are C++, Ruby, PHP, Swift, Kotlin, Scala, Lua, Haskell, Elixir, Clojure, and more. Unsupported languages fall back to line-based chunking.

## Can I use a different embedding model?

Yes. Any model available through Ollama works. Update `config.yaml` with the model name and dimensions, then pull it with `ollama pull <model>`.

## Does it work with large codebases?

Yes. Postgres handles millions of rows and vectors. Ingest directories recursively and let the pipeline batch-process everything.

## What editors are supported?

Any MCP-compatible editor: OpenCode, Claude Code, Cursor, VS Code with Cline, and others.

## How do I switch from HTTP mode to MCP stdio mode?

Run `corpus-kb --transport stdio`. The server speaks MCP over stdin/stdout and registers the catalogue operations plus the research/coding/release surfaces as tools. The config files in `mcp-configs/` launch exactly this command. HTTP stays available side by side: `corpus-kb start --transport http --port 8010`.

## Where is the configuration file?

The loader checks `./config.yaml`, the packaged default inside `src/corpus_kb/config.yaml`, and the installer config under `~/.corpus-kb`. Environment variables override any value.

## How do I back up my data?

Use `pg_dump` to back up the Postgres database. The event store is inside Postgres, so the backup captures both events and projections.

---

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [Ingestion strategies](INGESTION-STRATEGIES.md)
- [Admin guide](ADMIN.md)
- [API reference](API.md)
- [Development guide](DEVELOPMENT.md)

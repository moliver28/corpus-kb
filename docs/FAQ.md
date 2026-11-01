# FAQ

Common questions about Corpus-KB.

---

## Do I need Postgres?

Not always. Postgres 17 with pgvector and Apache AGE is the default engine. The DuckDB engine stores everything in one local file and needs no Docker. Both are first-class; see [Storage backends](STORAGE-BACKENDS.md). Earlier prototypes used LanceDB, DuckDB, and SQLite; DuckDB returned as a full engine in the storage-portability program.

## Do I need a GPU?

No. The shipped default `qwen3-embedding:4b` runs on CPU. A GPU makes ingest and search faster, but nothing requires one.

## Can I use OpenAI embeddings instead of Ollama?

Not out of the box. The built-in providers are Ollama and PostgresML (`embedding.provider: pgml`). Adding a cloud embedder would require a code change, and the local-first design does not call out to one.

## Can I run without Ollama?

Yes. Ingest continues when Ollama is unreachable, but the affected chunks are stored with zero vectors and flagged `degraded`. Search leaves degraded chunks out of vector results by default (`retrieval.exclude_degraded: true`). It reports what it excluded in the `degraded_excluded` field of the response, and still runs the full-text arm. Once Ollama is back, `corpus-kb ingest-reembed` re-embeds the flagged chunks and clears the flag.

## Can I teach it a new file format?

Yes — ingestion is pluggable at three levels. Map a source type to an existing strategy in `config.yaml` (no code). Register a strategy in-process with `register_strategy()`. Ship one as an entry-point plugin from your own package. See [Ingestion strategies](INGESTION-STRATEGIES.md).

## How is this different from a cloud vector database like Pinecone?

Corpus-KB is 100% local. Your code never leaves your machine. It also gives you three query patterns (vector search, SQL, and graph traversal), not vector retrieval alone.

## How is this different from ripgrep?

Ripgrep finds exact string matches. Corpus-KB finds conceptually related code even when the keywords differ. It also understands structure: functions, classes, headings, and entity relationships.

## What is event sourcing?

Event sourcing means the event store is the source of truth. Every change is recorded as an immutable event. Projections read those events and build the Postgres read models. This gives you an audit trail, the ability to replay history, and a clean separation between writes and reads.

## How do I version my data?

Because every change is an event, you can list versions, tag important points, and check out or restore earlier states. Projections can be rebuilt from any point in the event stream.

## What languages does tree-sitter support?

Python, JavaScript, TypeScript, JSX, TSX, Rust, Go, Java, and C are supported. So are C++, Ruby, PHP, Swift, Kotlin, Scala, Lua, Haskell, Elixir, Clojure, and more. Unsupported languages fall back to line-based chunking.

## Can I use a different embedding model?

Yes. Any model available through Ollama works. Update `config.yaml` with the model name and dimensions, then pull it with `ollama pull <model>`. One rule applies: the schema column is fixed at `halfvec(2560)`, so the model must emit exactly 2560 dimensions. Another width needs a schema migration first. The ingest guard and `corpus-kb doctor` catch a mismatch and name the fix.

## Does it work with large codebases?

Yes. Postgres handles millions of rows and vectors. Ingest directories recursively and let the pipeline batch-process everything.

## What editors are supported?

Any MCP-compatible client: Claude Code, Cursor, Codex, OpenCode, Hermes, Cline, and others. The `mcp-configs/` files cover Claude Code, Cursor, and Codex; OpenCode uses the root `opencode.json`. Generic MCP clients need no config file — they point at `corpus-kb --transport stdio`. See [harness-distribution.md](research/harness-distribution.md).

## How do I switch from HTTP mode to MCP stdio mode?

Run `corpus-kb --transport stdio`. The server speaks MCP over stdin/stdout and registers every catalogue operation as a tool. The config files in `mcp-configs/` launch exactly this command. HTTP stays available side by side: `corpus-kb start --transport http --port 8010`.

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

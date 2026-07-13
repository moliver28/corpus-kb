# FAQ

Common questions about Corpus-KB.

---

## Do I need PostgreSQL?

Yes. The current release requires PostgreSQL 17 with pgvector and Apache AGE. Earlier prototypes used LanceDB, DuckDB, and SQLite, but the production storage layer is now Postgres.

## Do I need a GPU?

No. The default model, `nomic-embed-text`, runs well on CPU. It is about 274 MB and produces 768-dimensional embeddings.

## Can I use OpenAI embeddings instead of Ollama?

Not out of the box. Corpus-KB currently calls Ollama's local embedding API. You could add a new embedder implementation that calls OpenAI, but that would require a code change.

## Can I run without Ollama?

Yes, but search falls back to full-text only. The ingest pipeline continues with zero-vector embeddings when Ollama is unreachable, and it records the failure in the response.

## How is this different from a cloud vector database like Pinecone?

Corpus-KB is 100% local. Your code never leaves your machine. It also gives you three query patterns (vector search, SQL, and graph traversal) rather than just vector retrieval.

## How is this different from ripgrep?

Ripgrep finds exact string matches. Corpus-KB finds conceptually related code even when the keywords differ. It also understands structure: functions, classes, headings, and entity relationships.

## What is event sourcing?

Event sourcing means the event store is the source of truth. Every change is recorded as an immutable event. Projections read those events and build the Postgres read models. This gives you an audit trail, the ability to replay history, and a clean separation between writes and reads.

## How do I version my data?

Because every change is an event, you can list versions, tag important points, and check out or restore earlier states. Projections can be rebuilt from any point in the event stream.

## What languages does tree-sitter support?

Python, JavaScript, TypeScript, JSX, TSX, Rust, Go, Java, C, C++, Ruby, PHP, Swift, Kotlin, Scala, Lua, Haskell, Elixir, Clojure, and more. Unsupported languages fall back to line-based chunking.

## Can I use a different embedding model?

Yes. Any model available through Ollama works. Update `config.yaml` with the model name and dimensions, then pull it with `ollama pull <model>`.

## Does it work with large codebases?

Yes. Postgres handles millions of rows and vectors. Ingest directories recursively and let the pipeline batch-process everything.

## What editors are supported?

Any MCP-compatible editor: OpenCode, Claude Code, Cursor, VS Code with Cline, and others.

## How do I switch from HTTP mode to MCP stdio mode?

Start the server with `--transport stdio`:

```bash
corpus-kb --transport stdio
```

## Where is the configuration file?

The loader checks `./config.yaml`, `./corpus-kb/config.yaml`, and `~/.corpus-kb/config.yaml`. Environment variables override any value.

## How do I back up my data?

Use `pg_dump` to back up the Postgres database. The event store is inside Postgres, so the backup captures both events and projections.

---

## Next steps

- [Install guide](INSTALL.md)
- [Features overview](FEATURES.md)
- [Admin guide](ADMIN.md)
- [API reference](API.md)
- [Development guide](DEVELOPMENT.md)

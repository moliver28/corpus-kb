# Corpus-KB FAQ

Questions people ask about Corpus-KB, with short answers. This file is
sample content for the retrieval demo. The answers are honest about how
the system works.

## Where do my answers come from?

Every answer is built from chunks of your own files. The system finds
candidate chunks, fuses the rankings, and generates sentences from the
winners. Each sentence carries a citation back to the source document.

## Does anything leave my machine?

No. The server, the database, and the models run locally. Embeddings come
from your local Ollama instance, or from PostgresML inside your database.

## What happens when the embedding model is down?

Ingest continues with degraded vectors and flags the affected chunks.
Search leaves those chunks out of vector results by default. The
`ingest-reembed` command repairs them after the model returns.

## How do I ask a question?

Three ways exist.

- The **Ask** page in the browser.
- `corpus-kb ask` in a terminal.
- The `ask` tool over MCP.

All three return the same envelope with citations.

## What is a chunk?

A chunk is one coherent piece of a document. It can be a section, a
slide, a code function, or a speaker turn. Chunks are the unit of search
and citation.

## Can I watch what the server does?

Yes. The **Setup** page shows the doctor's health checks in plain
language. OpenTelemetry tracing can export spans to any OTLP collector.

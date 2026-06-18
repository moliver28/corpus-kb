# Corpus-KB: Decisions Record

## Purpose

This file records all critical decisions made during the planning phase.
A future execution session reads this to avoid re-asking questions.

---

## User Answers

| Question | Answer | Implication |
|----------|--------|-------------|
| Do transcripts need audio transcription? | **No — already text files** | No whisper/audio pipeline. Ingest is file-based: read → chunk → embed → store. |
| Typical code file size / chunking approach? | **Max chunk preserving hierarchy/semantics** | Structure-aware chunking: AST for code, headings for markdown, semantic for text. Never split mid-function/class. Preserve parent/child/sibling relationships. |

## Architectural Decisions (No User Input Required)

| Decision | Choice | Core Reason |
|----------|--------|-------------|
| MCP SDK | Python + FastMCP | ML ecosystem. Decorator-based. Auto schema. |
| Vector DB | LanceDB | Embedded. Git-like versioning. Hybrid search. SQL filters. |
| SQL | DuckDB over LanceDB | Native integration. Full SQL. Zero extra infra. |
| Embeddings | Ollama + nomic-embed-text | Single binary. Local. Private. Good quality. |
| Code chunking | tree-sitter AST-aware | 40+ languages. Never splits mid-function. Scope chains. |
| Graph | Abstract GraphStore interface | SQLite (L1) day one. GraphQLite (L2) via pip. LatticeDB (L3) future. MCP tools never change. |
| Versioning | LanceDB built-in + Git | Data versioning baked in. No DVC needed. |
| Collaboration | MCP Streamable HTTP | Architected from start. MVCC for concurrency. |

## Graph Graduation Path

```
Current:     Level 1 — SQLite (ships day one)
Week 2:      Level 2 — GraphQLite (pip install, same .db file, Cypher + algorithms)
Future:      Level 3 — LatticeDB (when Python bindings mature, single-file graph+vector+FTS)
```

## Next Execution Session Should Start At

**Phase 0 — Project Scaffolding.** All decisions are made. No questions to ask.

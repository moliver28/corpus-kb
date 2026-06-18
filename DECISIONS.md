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
| Embeddings | Ollama + qwen3-embedding:8b-q8_0 | 8B params, 4096d, MTEB #1 (70.58), 100+ languages, 32K context. q8_0 quantization near-lossless vs fp16. |
| Relational DB | Persistent DuckDB (file-backed) | Full schema (documents, chunks, tags, metadata). Full CRUD with safety rails. Auto-sync from LanceDB. |
| Code chunking | tree-sitter AST-aware | 40+ languages. Never splits mid-function. Scope chains. |
| Graph | Abstract GraphStore interface | SQLite (L1) day one. GraphQLite (L2) via pip. Apache AGE (L3a) for PG users. LatticeDB (L3b) future. MCP tools never change. |
| Versioning | LanceDB built-in + Git | Data versioning baked in. No DVC needed. |
| Collaboration | MCP Streamable HTTP | Architected from start. MVCC for concurrency. |

## Graph Graduation Path

```
Current:     Level 1  — SQLite        (ships day one, zero deps)
Week 2:      Level 2  — GraphQLite    (pip install, same .db file, Cypher + algorithms)
Month 2:     Level 3a — Apache AGE    (PostgreSQL extension, production openCypher)
Future:      Level 3b — LatticeDB     (when Python bindings mature, single-file graph+vector+FTS)
```

**Choosing L3a vs L3b:**
- **Apache AGE** — for users already running PostgreSQL or needing production-grade graph at scale. Adds operational overhead (PG server, extension install). Hybrid SQL+Cypher queries are a unique advantage.
- **LatticeDB** — for users wanting zero-config, single-file graph. Promised Python bindings are pre-release; will adopt when stable. Removes need for any server.

## Next Execution Session Should Start At

**Phase 0 — Project Scaffolding.** All decisions are made. No questions to ask.

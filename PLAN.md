# Corpus-KB — Master Plan

Local RAG system exposing MCP tools to agentic code editors (OpenCode, Claude Code, Cursor, Codex). 34 tools across ingest, search, relational DB, graph, and versioning.

---

## Status Dashboard

> **Built:** Everything below is shipped and working (demo verified end-to-end).

| Layer | Status | Tools | Key File |
|-------|--------|-------|----------|
| **Ingest** | ✅ Done | `ingest_file`, `ingest_text`, `ingest_directory`, `list_documents`, `delete_document` | `src/tools/ingest_tools.py` |
| **Search** | ✅ Done | `search`, `search_similar`, `retrieve_context`, `search_context` | `src/tools/search_tools.py` |
| **Relational DB** | ✅ Done | `sql_query`, `sql_execute`, `sql_tables`, `add_tag`, `tag_document`, `untag_document`, `get_document_tags`, `set_metadata`, `get_metadata`, `sync_database`, `query_document_stats` | `src/tools/database_tools.py`, `src/storage/duckdb_engine.py` |
| **Graph (L1)** | ✅ Done | `add_entity`, `add_relation`, `search_graph`, `bfs`, `get_entity_relations` | `src/tools/graph_tools.py`, `src/storage/graph_store.py` |
| **Versioning** | ✅ Done | `list_versions`, `create_tag`, `get_stats`, `checkout_version`, `restore_version`, `create_branch`, `list_branches`, `switch_branch` | `src/tools/version_tools.py` |
| **Chunking** | ✅ Done | Code (AST, 40+ langs), Markdown (heading-aware), Text (semantic) | `src/chunking/` |
| **Embedding** | ✅ Done | qwen3-embedding:8b-q8_0 (4096d, MTEB #1), batch + SHA256 cache | `src/rag/embedder.py` |

---

## Roadmap — GitHub Issues

| # | Area | Issue | Priority |
|---|------|-------|----------|
| 1 | **Editor integration** | [Connect from OpenCode / Claude Code / Cursor](https://github.com/moliver28/corpus-kb/issues/1) | ⭐ High |
| 2 | **CI pipeline** | [GitHub Actions, tests on push](https://github.com/moliver28/corpus-kb/issues/3) | ⭐ High |
| 3 | **Scale test** | [Ingest real codebases, performance targets](https://github.com/moliver28/corpus-kb/issues/8) | ⭐ High |
| 4 | **PyPI publish** | [`pip install corpus-kb`](https://github.com/moliver28/corpus-kb/issues/2) | 🟡 Medium |
| 5 | **Graph L2** | [GraphQLite: Cypher + PageRank + Louvain](https://github.com/moliver28/corpus-kb/issues/4) | 🟡 Medium |
| 6 | **Graph L3a** | [Apache AGE: production openCypher on PG](https://github.com/moliver28/corpus-kb/issues/5) | 🔵 Low |
| 7 | **Graph L3b** | [LatticeDB: single-file graph+vector (future)](https://github.com/moliver28/corpus-kb/issues/6) | 🔵 Low |
| 8 | **Collaboration** | [MCP Streamable HTTP transport](https://github.com/moliver28/corpus-kb/issues/7) | 🔵 Low |

---

## Repository Map

```
F:\Documents\OpenCode\Corpus\
├── PLAN.md                  ← You are here
├── DECISIONS.md             Architectural decisions record
├── config.yaml              Tunable parameters (embedding, chunking, search, graph, database)
├── pyproject.toml            Package metadata + dependencies
│
├── src/
│   ├── server.py             FastMCP entrypoint (CLI: --transport stdio|sse)
│   ├── config.py             Config loader (env overrides CORPUS_KB_*)
│   │
│   ├── tools/                MCP tool implementations (34 tools)
│   │   ├── ingest_tools.py      5 tools
│   │   ├── search_tools.py      4 tools
│   │   ├── database_tools.py    11 tools
│   │   ├── graph_tools.py       5 tools
│   │   ├── sql_tools.py         1 tool (legacy compat)
│   │   └── version_tools.py     8 tools
│   │
│   ├── storage/              Storage engines
│   │   ├── lancedb_store.py      LanceDB (vectors, versioning, hybrid search, 4096d)
│   │   ├── duckdb_engine.py      Persistent DuckDB (relational schema, auto-sync, tags, metadata)
│   │   └── graph_store.py        Abstract GraphStore + SQLite L1 + factory
│   │
│   ├── rag/                  Retrieval
│   │   ├── embedder.py           Ollama (qwen3-embedding, batching, SHA256 cache)
│   │   ├── hybrid_search.py      Vector + FTS + RRF fusion
│   │   └── reranker.py           Identity + LLM reranking
│   │
│   ├── chunking/             Splitting strategies
│   │   ├── code_chunker.py       AST-aware (tree-sitter, 40+ langs)
│   │   ├── markdown_chunker.py   Heading-aware
│   │   ├── text_chunker.py       Paragraph + semantic (uses qwen3-embedding)
│   │   ├── detector.py           Auto-detect file type → chunker
│   │   └── hierarchy.py          Parent/sibling/heading resolver
│   │
│   └── utils/
│       └── models.py             Pydantic models (Chunk, Document, SearchResult, etc.)
│
├── tests/                   72 tests (all passing)
│   ├── test_chunking.py         32 tests
│   ├── test_rag.py              18 tests
│   └── test_integration.py      23 tests
│
├── scripts/                 Setup + demo
│   ├── setup.ps1                Windows one-click
│   ├── setup.sh                 Mac/Linux one-click
│   └── demo.py                  End-to-end walkthrough (verified)
│
└── mcp-configs/              Cursor → OpenCode → Claude Code
    ├── opencode.json
    ├── claude-code.json
    └── cursor.json
```

---

## Architecture (30-second summary)

```
Editor (OpenCode / Cursor / Claude Code)
    │ MCP stdio
    ▼
FastMCP Server ─── 34 tools ─── 6 resources
    │
    ├── LanceDB     ← vectors + versioning + hybrid search
    ├── DuckDB      ← relational schema + tags + metadata (auto-synced)
    └── SQLite      ← graph entities + relations (pluggable: L2→GraphQLite, L3a→AGE, L3b→LatticeDB)
```

**Key numbers:** 4096d embeddings (qwen3-embedding), 72 tests, 100% local (Ollama), zero Docker.

---

## Quick Start

```bash
# Prerequisites: Python 3.11+, Ollama running with qwen3-embedding:8b-q8_0
pip install -e .
ollama pull qwen3-embedding:8b-q8_0       # 8GB, ~5 min
python scripts/demo.py                      # Verify everything works
python src/server.py                        # Start MCP server (stdio)
```

See [DECISIONS.md](DECISIONS.md) for architectural decisions.
See [GitHub Issues](https://github.com/moliver28/corpus-kb/issues) for what's next.

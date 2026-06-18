# Corpus-KB

[![CI](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml/badge.svg)](https://github.com/moliver28/corpus-kb/actions/workflows/ci.yml)

Local end-to-end RAG system for agentic code editors.

Exposes retrieval-augmented generation capabilities via MCP (Model Context Protocol)
to any MCP-compatible client: OpenCode, Claude Code, Cursor, Codex, and others.

## Features

- **Hybrid Search** — Vector similarity + full-text search fused via Reciprocal Rank Fusion
- **Structure-Aware Chunking** — AST-aware for code (tree-sitter, 40+ languages),
  heading-aware for markdown, semantic for plain text
- **Hierarchical Context** — Parent/child/sibling chunk relationships for context-aware retrieval
- **Relational SQL** — DuckDB queries over document metadata and chunk contents
- **Entity Graph** — SQLite-backed graph with entity/relation tracking, upgradable to
  GraphQLite (Cypher) or LatticeDB
- **Data Versioning** — LanceDB's built-in Git-like versioning with branches, tags, and time-travel
- **100% Local** — Ollama for embeddings, no cloud dependencies, no Docker required

## Quick Start

```bash
# Install dependencies
pip install -e .

# Pull embedding model
ollama pull nomic-embed-text

# Run the server
python -m src.server
```

## MCP Configuration

See `mcp-configs/` for client configuration files.

| Client | Config File |
|--------|-------------|
| OpenCode | `mcp-configs/opencode.json` |
| Claude Code | `mcp-configs/claude-code.json` |
| Cursor | `mcp-configs/cursor.json` |

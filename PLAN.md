# Corpus-KB: Local End-to-End RAG System

## Status: Plan — Ready to Execute

This document is the complete, executable architecture plan for a local RAG system
that exposes tools via MCP to agentic code editors (OpenCode, Claude Code, Cursor, Codex).

---

## Table of Contents

1. [Architecture Decisions](#1-architecture-decisions)
2. [System Architecture](#2-system-architecture)
3. [Chunking Strategy](#3-chunking-strategy)
4. [Data Model](#4-data-model)
5. [MCP Tools & Resources](#5-mcp-tools--resources)
6. [Project Structure](#6-project-structure)
7. [Implementation Phases](#7-implementation-phases)
8. [Graph Graduation Roadmap](#8-graph-graduation-roadmap)
9. [Configuration](#9-configuration)
10. [Setup & Dependencies](#10-setup--dependencies)

---

## 1. Architecture Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| **MCP SDK** | Python + FastMCP | ML ecosystem (sentence-transformers, numpy, tree-sitter). FastMCP gives decorator-based tools, auto JSON schema from type hints, stdio + HTTP transports. Built into official Python MCP SDK. |
| **Vector DB** | LanceDB | Embedded (no server, no Docker). Automatic Git-like versioning (branches, tags, time-travel). Hybrid search (vector + full-text + RRF). Columnar storage handles 100M+ vectors on a laptop. SQL filtering for relational queries. Free OSS. |
| **Relational SQL** | DuckDB over LanceDB | DuckDB reads LanceDB tables natively. Full SQL for complex joins, aggregations, filters. Zero extra infra. |
| **Embeddings** | Ollama + nomic-embed-text (768d) | Single binary. Auto model management. Local and private. MTEB 62.4 quality. OpenAI-compatible API. |
| **Chunking (code)** | AST-aware via tree-sitter | Parses 40+ languages. Splits at function/class/method boundaries. Never splits mid-expression. Preserves scope chain, imports, hierarchy. |
| **Chunking (markdown)** | Heading-aware section splitter | Chunks at `#`/`##`/`###` boundaries. Preserves heading path and nesting level. |
| **Chunking (text)** | Semantic similarity splitter | Sentence embedding windows + cosine gap detection. Splits at topic shifts. |
| **Graph** | Abstract `GraphStore` interface | Plug-in architecture: SQLite (Level 1) day one, GraphQLite Cypher (Level 2) via `pip install`, LatticeDB (Level 3) future. MCP tools never change. |
| **Data Versioning** | LanceDB built-in + Git | LanceDB tracks every write as immutable version with branches/tags. Git tracks server code + config. |
| **Collaboration** | MCP Streamable HTTP | Architected from day one. LanceDB MVCC supports concurrent readers/writers. |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     MCP CLIENTS                                  │
│  OpenCode │ Claude Code │ Cursor │ Codex │ any MCP-compatible   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP Protocol (stdio / Streamable HTTP)
┌──────────────────────────▼──────────────────────────────────────┐
│                    MCP SERVER (Python FastMCP)                   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Tool Layer                          Resource Layer       │   │
│  │  ┌──────────┐┌──────────┐┌─────────┐ ┌────────┐┌─────┐  │   │
│  │  │ ingest   ││ search   ││ query   │ │ doc:// ││graph│  │   │
│  │  │ manage   ││ graph    ││ version │ │stats://││...  │  │   │
│  │  └──────────┘└──────────┘└─────────┘ └────────┘└─────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  RAG Engine                                                │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │   │
│  │  │ Chunker      │ │ Embedder     │ │ Hybrid Searcher  │  │   │
│  │  │ (code/md/text│ │ (Ollama      │ │ (LanceDB FTS     │  │   │
│  │  │  auto-detect)│ │  nomic-embed)│ │  + vector + RRF) │  │   │
│  │  └──────────────┘ └──────────────┘ └──────────────────┘  │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │   │
│  │  │ Graph Engine │ │ SQL Engine   │ │ Version Manager │  │   │
│  │  │ (abstract    │ │ (DuckDB on   │ │ (LanceDB branch │  │   │
│  │  │  GraphStore) │ │  LanceDB)    │ │  / tag / time-  │  │   │
│  │  │              │ │              │ │  travel)        │  │   │
│  │  └──────────────┘ └──────────────┘ └──────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────┬───────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────┐
│                       STORAGE LAYER                              │
│                                                                  │
│  ~/.corpus-kb/                                                   │
│  ├── lancedb/                 # Primary: vectors + metadata      │
│  │   ├── chunks.table         #   Text chunks + embeddings       │
│  │   ├── documents.table      #   Document metadata              │
│  │   └── entities.table       #   Entity embeddings (optional)   │
│  ├── graph.db                 # Graph: SQLite + GraphQLite later  │
│  ├── config.yaml              # Server configuration              │
│  └── logs/                    # Ingestion/query logs              │
│                                                                  │
│  Git Repository (separate from data dir)                         │
│  - MCP server code      - Config files                           │
│  - Ingestion pipelines  - Migration scripts                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Chunking Strategy

This is the most important design decision for retrieval quality.

### 3.1 File Type Detection

```
detect_file_type(path) -> "code" | "markdown" | "text"
  - Check file extension against known code/markdown lists
  - Check shebang line for code files
  - Fallback to "text" for unknown types
```

### 3.2 Code Chunking — AST-aware via tree-sitter

```
Source: large_file.py
├── imports chunk              # All imports grouped
├── class UserService          # Complete class, never split
│   ├── def __init__
│   ├── def get_user
│   └── def create_user
├── class EmailService         # Complete class
│   ├── def send_email
│   └── def validate_email
├── def helper_function        # Standalone function
└── def main()                 # Entry point

Each chunk stores:
  { type: "class" | "function" | "method",
    name: "UserService",
    scope_chain: ["UserService"],
    parent_name: null,
    imports: ["from db import ..."],
    file_path: "src/services.py",
    start_line: 10, end_line: 85,
    text: "...complete entity..." }
```

- Uses **tree-sitter** to parse AST — 40+ languages supported
- Extracts complete syntactic units: functions, classes, methods, interfaces
- **Never splits mid-function or mid-class**
- Merges small adjacent units (e.g., 3 small helpers) into one chunk
- Splits oversized units at internal statement boundaries
- Each chunk carries `scope_chain`, `parent_name`, `imports`, `file_path`, `start/end_line`

### 3.3 Markdown Chunking — Heading-aware

```
Source: doc.md
├── # Title                     → 1 chunk: [title, heading_path=["# Title"]]
├── ## Installation             → 1 chunk: [text, heading_path=["# Title", "## Installation"]]
├── ## Configuration            → 1 chunk: [text, heading_path=["# Title", "## Configuration"]]
└── ## Usage                    → 1 chunk: [text, heading_path=["# Title", "## Usage"]]

Each chunk stores:
  { heading_path: ["# Title", "## Installation"],
    level: 2,
    parent_heading: "# Title",
    text: "complete section text..." }
```

- Splits at `#`, `##`, `###` heading boundaries
- Each section becomes one "max" chunk
- Stores `heading_path` array for hierarchical navigation
- If a section exceeds max token limit, sub-splits at next heading level

### 3.4 Text Chunking — Semantic similarity

```python
1. Split into sentences
2. Group sentences into windows (3-5 sentences)
3. Compute embedding for each window
4. Find cosine distance between adjacent windows
5. Split at the largest distance gaps (= topic shifts)
```

- Adaptive chunk size: each chunk is one coherent topic
- Falls back to paragraph boundaries if embedding not available
- Position indices enable neighbor expansion during retrieval

### 3.5 Hierarchy-Aware Retrieval

When a query matches, the system can:
1. Return the exact matching chunk (precise)
2. **Expand upward** to include parent context (section heading, class scope)
3. **Expand sideways** to include sibling chunks (other methods, adjacent sections)
4. Configurable depth via `expand_context` parameter per query

---

## 4. Data Model

### 4.1 LanceDB Tables

**`documents` table**
| Column | Type | Description |
|--------|------|-------------|
| doc_id | string | UUID |
| source | string | File path or title |
| source_type | string | file, text, url, transcript |
| metadata | JSON | User-provided metadata |
| created_at | timestamp | Ingestion time |
| chunk_count | int | Number of chunks |
| version | int | LanceDB auto-increment |

**`chunks` table**
| Column | Type | Description |
|--------|------|-------------|
| chunk_id | string | UUID |
| doc_id | string | Parent document |
| text | string | Chunk content |
| vector | float32[768] | Embedding |
| chunk_index | int | Position in document |
| source | string | Document source |
| metadata | JSON | Inherited + chunk-level |
| **heading_path** | JSON | Hierarchy path (e.g., ["# Title", "## Section"]) |
| **parent_chunk_id** | string | If child of another chunk |
| **sibling_order** | int | Order among siblings |
| **scope_chain** | JSON | Code scope (e.g., ["UserService", "get_user"]) |
| **chunk_type** | string | function, class, method, section, paragraph |
| **entity_name** | string | Function/class name if code |
| created_at | timestamp | |

### 4.2 SQLite Graph Tables (graph.db)

```sql
CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,          -- Person, Place, Class, Function, Concept, etc.
    metadata JSON,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE relations (
    relation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES entities(entity_id),
    target_id TEXT NOT NULL REFERENCES entities(entity_id),
    relation_type TEXT NOT NULL,  -- CONTAINS, DEPENDS_ON, CALLS, KNOWS, etc.
    weight REAL DEFAULT 1.0,
    metadata JSON,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_relations_source ON relations(source_id);
CREATE INDEX idx_relations_target ON relations(target_id);
CREATE INDEX idx_relations_type ON relations(relation_type);
```

### 4.3 DuckDB (ad-hoc queries over LanceDB)

DuckDB can connect to LanceDB tables natively. No separate schema needed.

---

## 5. MCP Tools & Resources

### 5.1 Tools

#### Ingest Tools
| Tool | Input | Description |
|------|-------|-------------|
| `ingest_text` | `text, source, file_type` | Auto-detect type → chunk → embed → store |
| `ingest_file` | `path` | Detect, chunk, embed, store. Returns doc_id |
| `ingest_directory` | `path, pattern, recursive` | Batch process files matching glob. Returns list of doc_ids |
| `list_documents` | — | List all ingested documents with stats |
| `delete_document` | `doc_id` | Remove doc and all its chunks |

#### Search Tools
| Tool | Input | Description |
|------|-------|-------------|
| `search` | `query, k, filters, expand_context` | Hybrid search (vector + FTS + RRF). Optional context expansion to parent/siblings |
| `search_similar` | `chunk_id, k` | Find semantically similar chunks to a given one |
| `retrieve_context` | `query, k, filters` | Returns formatted context string + source citations for LLM prompt building |
| `query_sql` | `sql` | Execute arbitrary DuckDB SQL over document/chunk metadata |

#### Graph Tools
| Tool | Input | Description |
|------|-------|-------------|
| `query_graph` | `query, type` | Search entities by name/type |
| `get_entity` | `entity_id` | Entity details + all direct relations |
| `traverse_graph` | `entity_id, depth` | Multi-hop BFS traversal with cycle detection |
| `cypher_query` | `query` | **(Level 2+)** Raw Cypher query. Returns NotImplemented at Level 1 |
| `pagerank` | — | **(Level 2+)** PageRank scores. Returns NotImplemented at Level 1 |
| `louvain` | — | **(Level 2+)** Community detection. Returns NotImplemented at Level 1 |
| `shortest_path` | `from_id, to_id` | **(Level 2+)** Dijkstra shortest path. Returns NotImplemented at Level 1 |

#### Version Tools
| Tool | Input | Description |
|------|-------|-------------|
| `list_versions` | — | Show version history (like `git log`) |
| `checkout_version` | `version` | Query data at specific point in time (read-only) |
| `restore_version` | `version` | Rollback to previous version (creates new commit) |
| `tag_version` | `version, tag_name` | Create immutable tag (like `git tag`) |
| `create_branch` | `branch_name, from_version` | Isolated branch for experimentation |
| `list_branches` | — | Show all branches |
| `switch_branch` | `branch_name` | Switch active branch |
| `get_stats` | — | DB statistics: total docs, chunks, size, current version |

### 5.2 Resources

| Resource URI | Content |
|-------------|---------|
| `doc://{doc_id}` | Full document with all chunks + hierarchy |
| `chunk://{chunk_id}` | Single chunk + context (parent, siblings, heading path) |
| `graph://{entity_id}` | Entity with all relations |
| `search://{query}` | Search results as structured text |
| `versions://` | Version tree |
| `stats://` | Database statistics |

---

## 6. Project Structure

```
corpus-kb/
├── pyproject.toml              # Python project config + dependencies
├── README.md
├── config.yaml                 # All tunable parameters
│
├── src/
│   ├── server.py               # FastMCP server entrypoint + transport config
│   ├── config.py               # YAML config loader
│   │
│   ├── chunking/               # ★ The core innovation
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract Chunker interface
│   │   ├── code_chunker.py     # tree-sitter AST-aware chunker (40+ langs)
│   │   ├── markdown_chunker.py # Heading-aware section splitter
│   │   ├── text_chunker.py     # Semantic similarity chunker
│   │   ├── detector.py         # Auto-detect file type → pick chunker
│   │   └── hierarchy.py        # Parent/child/sibling metadata builder
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── embedder.py         # Ollama embedding client (batch, cache)
│   │   ├── hybrid_search.py    # LanceDB FTS + vector + RRF fusion
│   │   └── reranker.py         # Optional cross-encoder (off by default)
│   │
│   ├── storage/
│   │   ├── __init__.py         # create_graph_store() factory
│   │   ├── lancedb_store.py    # LanceDB CRUD, versioning, branches/tags
│   │   ├── duckdb_engine.py    # DuckDB SQL over LanceDB tables
│   │   └── graph_store.py      # Abstract GraphStore + SQLite implementation
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── extractor.py        # Simple entity extraction (regex + LLM)
│   │   └── traversal.py        # BFS/DFS with cycle detection
│   │
│   ├── tools/                  # MCP tool implementations
│   │   ├── __init__.py
│   │   ├── ingest_tools.py     # ingest_text, ingest_file, ingest_directory
│   │   ├── search_tools.py     # search, search_similar, retrieve_context
│   │   ├── graph_tools.py      # query_graph, traverse_graph, pagerank, etc.
│   │   ├── sql_tools.py        # query_sql
│   │   └── version_tools.py    # list_versions, checkout, restore, tag, branch
│   │
│   └── utils/
│       ├── __init__.py
│       └── models.py           # Pydantic models for all MCP tool I/O
│
├── tests/
│   ├── test_storage.py
│   ├── test_chunking.py
│   ├── test_search.py
│   └── test_graph.py
│
├── mcp-configs/                # One-click MCP client configs
│   ├── opencode.json
│   ├── claude-code.json
│   └── cursor.json
│
└── scripts/
    ├── setup.ps1               # Windows one-click setup
    ├── setup.sh                # Mac/Linux one-click setup
    └── demo.py                 # Demo: ingest sample files, run test queries
```

---

## 7. Implementation Phases

### Phase 0 — Project Scaffolding

**Files:** `pyproject.toml`, `config.yaml`, `README.md`, `mcp-configs/*.json`

```
1. mkdir corpus-kb/ with full directory tree
2. Write pyproject.toml with dependencies
3. Write config.yaml with defaults
4. Initialize git repo
5. Write MCP client configs for OpenCode, Claude Code, Cursor
```

**Dependencies (pyproject.toml):**
```toml
[project]
name = "corpus-kb"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "mcp[cli]>=1.0",
    "lancedb>=0.12",
    "duckdb>=1.0",
    "ollama>=0.4",
    "pyyaml>=6.0",
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "tree-sitter-javascript>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-rust>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-java>=0.23",
    "sentence-transformers>=3.0",   # fallback for text chunking
    "pydantic>=2.0",
    "rich>=13.0",                    # CLI output formatting
]
```

### Phase 1 — Storage Layer (~300 lines)

**Files:** `src/storage/lancedb_store.py`, `src/storage/duckdb_engine.py`, `src/storage/graph_store.py`

**LanceDBStore class:**
- `create_table(name, schema)` — create documents + chunks tables
- `insert_chunks(chunks)` — batch insert with versioning
- `insert_document(doc)` — insert document metadata
- `search_vector(query_embedding, k, filters)` — vector search with SQL filters
- `search_fts(query, k, filters)` — full-text search via LanceDB FTS
- `search_hybrid(query, embedding, k, filters, rrf_k)` — fused search
- `list_versions()` — return version history
- `checkout(version)` — time-travel to specific version
- `restore(version)` — rollback to specific version
- `create_tag(version, tag_name)` — create immutable tag
- `create_branch(name, from_version)` — create branch
- `get_stats()` — database statistics

**DuckDBEngine class:**
- `connect(lancedb_uri)` — connect to LanceDB tables
- `execute(sql)` — run arbitrary SQL query
- `get_tables()` — list available tables for SQL

**GraphStore (abstract) + SQLiteGraphStore (Level 1):**
- `add_entity(name, type, metadata) -> entity_id`
- `add_relation(source_id, target_id, rel_type, weight) -> relation_id`
- `get_entity(entity_id) -> entity`
- `get_neighbors(entity_id, depth=1) -> list[relation]`
- `bfs_traverse(start_id, max_depth=5) -> list[path]`
- `search_entities(query, type=None) -> list[entity]`
- `pagerank()` → raises NotImplementedError
- `louvain()` → raises NotImplementedError
- `cypher_query(query)` → raises NotImplementedError
- `shortest_path(from_id, to_id)` → raises NotImplementedError

### Phase 2 — Chunking Engine (~400 lines)

**Files:** `src/chunking/`

**detect_file_type(path) -> "code" | "markdown" | "text":**
- Check extension against code language map (.py, .js, .ts, .rs, .go, .java, etc.)
- Check extension against markdown (.md, .mdx, .rst)
- Check shebang for Python/JS/Shell
- Fallback to "text"

**CodeChunker:**
```python
class CodeChunker(Chunker):
    def __init__(self, max_size=2500, language_map=None):
        self.language_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".rs": "rust", ".go": "go", ".java": "java",
            ".cpp": "cpp", ".rb": "ruby", ".php": "php",
            ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
            # 40+ languages supported by tree-sitter
        }
        self.parsers = {}  # lazy-loaded tree-sitter parsers

    def chunk(self, text, file_path=None) -> list[Chunk]:
        # 1. Parse AST using tree-sitter
        # 2. Walk tree, extract entities (functions, classes, methods)
        # 3. Build scope tree (hierarchy of entities)
        # 4. Greedy pack entities into chunks ≤ max_size
        # 5. Merge small adjacent entities
        # 6. Split oversized entities at statement boundaries
        # 7. Enrich each chunk with: scope_chain, imports, file_path,
        #    start/end_line, parent_name, entity_name, chunk_type
```

**MarkdownChunker:**
```python
class MarkdownChunker(Chunker):
    def __init__(self, heading_levels=[1, 2, 3], max_section_size=3000):
        ...

    def chunk(self, text, file_path=None) -> list[Chunk]:
        # 1. Parse headings with regex (^#{1,3}\s+)
        # 2. Build heading tree
        # 3. Assign content under each heading to that section
        # 4. Create one Chunk per section with heading_path metadata
        # 5. If a section exceeds max_section_size, sub-split at child headings
        # 6. Each chunk carries: heading_path, level, parent_heading
```

**TextChunker:**
```python
class TextChunker(Chunker):
    def __init__(self, strategy="semantic", min_sentences=3, max_sentences=30):
        ...

    def chunk(self, text, file_path=None) -> list[Chunk]:
        # 1. Split into sentences using regex/sentence tokenizer
        # 2. Create sliding windows of sentences
        # 3. If semantic: embed windows, compute cosine gaps, split at
        #    largest gaps (topic shifts)
        # 4. If paragraph: split at double newlines, group
        # 5. If sentence: each chunk = sentences up to max_size
        # 6. Each chunk carries: chunk_index, position metadata
```

**ChunkHierarchy:**
```python
class ChunkHierarchy:
    def build(self, chunks) -> list[Chunk]:
        # Assign parent_id, child_ids, sibling_order, heading_path
        # to each chunk based on document structure
```

### Phase 3 — RAG Engine (~200 lines)

**Files:** `src/rag/embedder.py`, `src/rag/hybrid_search.py`, `src/rag/reranker.py`

**OllamaEmbedder:**
```python
class OllamaEmbedder:
    def __init__(self, model="nomic-embed-text", base_url="http://localhost:11434",
                 batch_size=64):
        self.client = ollama.Client(base_url)
        self.model = model
        self.batch_size = batch_size
        self.cache = {}  # LRU cache of text -> embedding

    def embed(self, text: str) -> list[float]:
        if text in self.cache:
            return self.cache[text]
        resp = self.client.embeddings(model=self.model, prompt=text)
        self.cache[text] = resp["embedding"]
        return resp["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Batch in groups of batch_size
        results = []
        for batch in chunks(texts, self.batch_size):
            resp = self.client.embed(model=self.model, input=batch)
            results.extend(resp["embeddings"])
        return results
```

**HybridSearcher:**
```python
class HybridSearcher:
    def __init__(self, store: LanceDBStore, embedder: OllamaEmbedder):
        self.store = store
        self.embedder = embedder

    def search(self, query: str, k: int = 10, filters: dict = None,
               expand_context: bool = False,
               context_depth: int = 1) -> list[SearchResult]:
        # 1. Embed query
        embedding = self.embedder.embed(query)
        # 2. Run vector search + FTS search in parallel
        vector_results = self.store.search_vector(embedding, k, filters)
        fts_results = self.store.search_fts(query, k, filters)
        # 3. Fuse with Reciprocal Rank Fusion
        fused = self._rrf_fuse(vector_results, fts_results, k=60)
        # 4. Optionally expand to parent/sibling chunks
        if expand_context:
            fused = self._expand_context(fused, context_depth)
        # 5. Return ranked results with scores
        return fused

    def _rrf_fuse(self, vector_results, fts_results, k=60):
        # Reciprocal Rank Fusion
        # score = sum(1 / (k + rank(doc)))
        ...

    def _expand_context(self, results, depth):
        # For each result, look up parent and sibling chunks
        # append them with context_type metadata
        ...
```

**Reranker:**
```python
class Reranker:
    def __init__(self, model_name="BAAI/bge-reranker-v2-m3"):
        # Load cross-encoder model
        # Off by default in config.yaml
        ...

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        # Compute cross-encoder scores
        ...
```

### Phase 4 — MCP Server + Tools (~300 lines)

**Files:** `src/server.py`, `src/tools/*.py`

```python
# src/server.py
from mcp.server.fastmcp import FastMCP
from src.config import load_config
from src.storage import create_graph_store, LanceDBStore, DuckDBEngine
from src.rag import OllamaEmbedder, HybridSearcher
from src.chunking import FileTypeDetector

config = load_config("config.yaml")
mcp = FastMCP("corpus-kb")

# Initialize components
store = LanceDBStore(config["storage"]["lancedb_uri"])
embedder = OllamaEmbedder(**config["embedding"])
searcher = HybridSearcher(store, embedder)
graph = create_graph_store(config)  # Factory: SQLite | GraphQLite | LatticeDB
sql = DuckDBEngine(config["storage"]["lancedb_uri"])
detector = FileTypeDetector()

# Register all tools
from src.tools.ingest_tools import register_ingest_tools
from src.tools.search_tools import register_search_tools
from src.tools.graph_tools import register_graph_tools
from src.tools.sql_tools import register_sql_tools
from src.tools.version_tools import register_version_tools

register_ingest_tools(mcp, store, embedder, detector)
register_search_tools(mcp, searcher)
register_graph_tools(mcp, graph)
register_sql_tools(mcp, sql)
register_version_tools(mcp, store)

# Register resources
@mcp.resource("doc://{doc_id}")
def get_document(doc_id: str) -> str: ...

@mcp.resource("chunk://{chunk_id}")
def get_chunk(chunk_id: str) -> str: ...

@mcp.resource("graph://{entity_id}")
def get_entity(entity_id: str) -> str: ...

@mcp.resource("stats://")
def get_stats() -> str: ...

if __name__ == "__main__":
    transport = config["server"]["transport"]  # "stdio" or "http"
    mcp.run(transport=transport)
```

### Phase 5 — Tests & Demo (~150 lines)

**Tests:**
- `test_storage.py` — LanceDB CRUD, versioning, branches
- `test_chunking.py` — Code/markdown/text chunking correctness
- `test_search.py` — Hybrid search + RRF fusion
- `test_graph.py` — Entity/relation CRUD, traversal

**Demo:**
```python
# scripts/demo.py
# 1. Ingest a sample Python project
# 2. Ingest a markdown doc
# 3. Search for a function
# 4. Retrieve context with hierarchy expansion
# 5. Query graph entities
# 6. List versions
```

### Phase 6 — Setup Scripts (~100 lines)

**scripts/setup.sh (Mac/Linux):**
```bash
#!/bin/bash
# 1. Check Python 3.11+
# 2. Create venv
# 3. pip install -e .
# 4. Check Ollama installed, pull nomic-embed-text
# 5. Install tree-sitter language parsers
# 6. Copy MCP client configs
# 7. Run smoke test
```

**scripts/setup.ps1 (Windows):**
```powershell
# Same logic in PowerShell
```

---

## 8. Graph Graduation Roadmap

The `GraphStore` abstract interface means MCP tools never change when you upgrade.

```
┌──────────────────────────────────────────────────────────────────┐
│  GRADUATION ROADMAP — Zero Tool Code Changes                     │
├────────────┬────────────────┬────────────────┬───────────────────┤
│            │ Level 1        │ Level 2        │ Level 3           │
│            │ SQLite         │ GraphQLite     │ LatticeDB         │
├────────────┼────────────────┼────────────────┼───────────────────┤
│ Ship date  │ Day 1          │ Week 2         │ Future (when      │
│            │                │                │  stable)          │
│ Install    │ built-in       │ pip install    │ pip install       │
│            │                │ graphqlite     │ latticedb         │
│ Migration  │ —              │ zero (same     │ export/import     │
│            │                │ .db file)      │ script needed     │
├────────────┼────────────────┼────────────────┼───────────────────┤
│ cypher_query│ NotImplemented│ ✅ Full Cypher │ ✅ Query lang     │
│ pagerank() │ NotImplemented │ ✅             │ ✅                │
│ louvain()  │ NotImplemented │ ✅             │ ✅                │
│ shortest_path│ NotImplemented│ ✅ Dijkstra   │ ✅                │
│ neighbors  │ ✅             │ ✅             │ ✅                │
│ bfs_traverse│ ✅            │ ✅             │ ✅                │
│ search_entities│ ✅         │ ✅             │ ✅                │
└────────────┴────────────────┴────────────────┴───────────────────┘
```

### How to Upgrade to Level 2 (GraphQLite)

```python
# 1. pip install graphqlite
# 2. Create GraphQLiteGraphStore implementing same GraphStore interface:

class GraphQLiteGraphStore(GraphStore):
    def __init__(self, db_path):
        from graphqlite import Graph
        self.g = Graph(db_path)

    def add_entity(self, name, type, metadata):
        entity_id = str(uuid4())
        self.g.upsert_node(entity_id, {"name": name, "type": type, **metadata})
        return entity_id

    def add_relation(self, source, target, rel_type, weight=1.0):
        self.g.upsert_edge(source, target, {"weight": weight}, rel_type=rel_type)

    def get_neighbors(self, entity_id, depth=1):
        results = self.g.query(f"""
            MATCH (n)-[r]-(m) WHERE n.id = '{entity_id}'
            RETURN m.id, m.name, type(r), r.weight
        """)
        return results

    def pagerank(self):
        return self.g.pagerank()

    def louvain(self):
        return self.g.louvain()

    def cypher_query(self, query):
        return self.g.query(query)

# 3. Change config.yaml: backend: graphqlite
# 4. Restart server
```

### How to Upgrade to Level 3 (LatticeDB — Future)

```python
# 1. pip install latticedb (when stable Python bindings available)
# 2. Implement LatticeDBGraphStore with same GraphStore interface
# 3. One-time export/import from SQLite to LatticeDB format
# 4. Change config.yaml: backend: latticedb
# 5. Restart server
```

---

## 9. Configuration

```yaml
# config.yaml — All tunable parameters

server:
  name: corpus-kb
  transport: stdio              # stdio | http
  host: localhost
  port: 8010

storage:
  path: ~/.corpus-kb
  lancedb_uri: ~/.corpus-kb/lancedb
  graph_db: ~/.corpus-kb/graph.db

embedding:
  provider: ollama
  model: nomic-embed-text
  base_url: http://localhost:11434
  batch_size: 64
  dimension: 768

chunking:
  max_size: 2048                # Max chars per chunk
  overlap: 100
  code:
    enabled: true
    max_size: 2500
    parser: tree-sitter
    supported_languages:
      - python, javascript, typescript, rust, go, java
      - cpp, ruby, php, swift, kotlin, scala
      - lua, haskell, elixir, clojure, sql
  markdown:
    enabled: true
    heading_levels: [1, 2, 3]
    max_section_size: 3000
  text:
    enabled: true
    strategy: semantic
    min_sentences: 3
    max_sentences: 30
  hierarchy:
    store_parent: true
    store_siblings: true
    max_context_depth: 3

search:
  hybrid: true
  rrf_k: 60
  expand_context: false
  context_expansion:
    include_parent: true
    include_siblings: true
    max_siblings: 5
  enable_reranker: false
  reranker_model: BAAI/bge-reranker-v2-m3

graph:
  backend: sqlite               # sqlite | graphqlite | latticedb (future)
  extract_entities: true
  extractor: regex              # regex | llm | hybrid
  max_traversal_depth: 5
```

---

## 10. Setup & Dependencies

### Prerequisites

- **Python 3.11+** — `winget install Python.Python.3.12` or `brew install python@3.12`
- **Ollama** — `winget install Ollama.Ollama` or `brew install ollama`
- **uv** — `pip install uv` (faster pip alternative)

### One-Command Setup (script will do all of this)

```bash
# 1. Clone and enter project
git clone <repo>
cd corpus-kb

# 2. Create virtual environment
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# 3. Install dependencies
uv pip install -e .

# 4. Install Ollama model
ollama pull nomic-embed-text

# 5. Run setup verification
python scripts/demo.py
```

### MCP Client Configuration

**OpenCode (opencode.json):**
```json
{
  "mcpServers": {
    "corpus-kb": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/corpus-kb", "src/server.py"]
    }
  }
}
```

**Claude Code (claude_desktop_config.json):**
```json
{
  "mcpServers": {
    "corpus-kb": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/corpus-kb", "src/server.py"]
    }
  }
}
```

**Cursor (.cursor/mcp.json):**
```json
{
  "mcpServers": {
    "corpus-kb": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/corpus-kb", "src/server.py"]
    }
  }
}
```

---

## Summary

| Metric | Value |
|--------|-------|
| Total files | ~25 |
| Total LOC | ~1,100 |
| Dependencies | 10 pip packages + Ollama |
| External services | 0 (Ollama is local process) |
| Docker needed | No |
| Graph levels | 3 (shipped: L1, upgrade: L2+L3) |
| Languages supported | 40+ code + markdown + text |
| Versioning | Built-in (LanceDB) |
| Collaboration | MCP HTTP transport (ready) |

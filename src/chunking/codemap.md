# src/chunking/

## Responsibility

Splits raw file content into semantically coherent `Chunk` objects, each carrying metadata (line ranges, entity names, scope chains, heading paths) for downstream embedding and retrieval. Acts as the content-preparation layer between file ingestion and vector storage.

## Design

### Strategy Pattern — `Chunker` ABC

`base.py` defines the `Chunker` abstract base class with a single method:

```python
def chunk(self, text: str, file_path: Optional[str] = None) -> list[Chunk]
```

Three concrete implementations implement this contract:

| Chunker | Strategy | Key constraint |
|---|---|---|
| `CodeChunker` | AST-aware via tree-sitter | Never split mid-function or mid-class |
| `MarkdownChunker` | Heading-boundary aware | Never split inside fenced code blocks |
| `TextChunker` | Semantic gap detection (optional) or paragraph-based | Split at topic boundaries, not arbitrary character limits |

### File Type Detection — `FileTypeDetector`

`detector.py` routes files to the correct chunker via a two-stage detection:

1. **Extension-based lookup** — `CODE_EXTENSIONS` dict maps 40+ extensions to language names; `MARKDOWN_EXTENSIONS` set identifies `.md`, `.mdx`, `.rst`.
2. **Shebang fallback** — For extensionless files, parses the first line (`#!`) against `SHEBANG_MAP` to detect `python`, `bash`, `ruby`, etc.
3. **Default** — Anything unmatched falls through to `"text"`.

`FileTypeDetector` holds a `{type: Chunker}` registry and exposes `chunk_file()` (detect + chunk in one call) and `chunk_text()` (chunk with optional type hint).

### Code Chunking — `CodeChunker`

Uses tree-sitter to parse source into an AST, then extracts named entities:

- **Entity extraction** — `_collect_entities()` walks the AST recursively, identifying language-specific constructs: `class_definition`, `function_definition`, `struct_item`, `impl_item`, `interface_declaration`, etc. Stops recursing into named entities to avoid nested duplication.
- **Import grouping** — `_extract_imports()` collects leading import statements (`import_statement`, `use_declaration`, etc.) into a dedicated `chunk_type="imports"` chunk.
- **Scope chains** — `_build_scope_chain()` traverses parent pointers to build the nesting path (e.g., `["UserService", "authenticate"]`).
- **Oversized splitting** — `_split_large_entity()` breaks entities exceeding `max_size` (default 2500 chars) at heuristic statement boundaries (`def `, `func `, `class `, `fn `, visibility modifiers).
- **Merge pass** — `_merge_small()` coalesces adjacent chunks under `min_size` (100 chars) to reduce fragmentation.
- **Fallback** — If tree-sitter parsing fails or the language is unsupported, falls back to line-based chunking that respects `def`/`class`/`fn` boundaries heuristically.

`LANGUAGE_MAP` maps file extensions to `(language_name, tree_sitter_package)` tuples. Currently covers: Python, JavaScript/JSX, TypeScript/TSX, Rust, Go, Java, C/C++, Ruby, PHP, Swift, Kotlin, Scala, Lua.

### Markdown Chunking — `MarkdownChunker`

Splits at ATX heading boundaries (`#` through `######`):

- **Frontmatter extraction** — `_extract_frontmatter()` strips and parses YAML frontmatter (`---...---`), attaching it to chunk metadata.
- **Section discovery** — `_find_sections()` builds a heading stack, computing `heading_path` (e.g., `["Features", "Hybrid search"]`) for each section. Handles heading nesting correctly: deeper headings pop when a shallower one appears.
- **Oversized splitting** — `_split_oversized_section()` breaks sections exceeding `max_size` (default 2048 chars) at paragraph boundaries (`\n\n+`).
- **Merge pass** — `_merge_small_siblings()` coalesces adjacent chunks sharing the same `heading_path`.

### Text Chunking — `TextChunker`

Two operational modes:

- **Paragraph mode** (default) — Splits at `\n\n+` boundaries, buffers paragraphs until `max_size` (default 2048 chars) is reached, then flushes.
- **Semantic mode** (`use_semantic=True`) — Splits text into sentences (respecting abbreviations like `Mr.`, `Dr.`, `etc.`), embeds each sentence via Ollama (`ollama.embed`), computes cosine similarity gaps between sliding windows of size `window_size` (default 3), and splits where the gap exceeds `gap_threshold` (default 0.3). Falls back to paragraph mode if Ollama is unreachable.

Sentence splitting uses a word-by-word scan with an abbreviation whitelist (`_ABBREVIATIONS`) to avoid false breaks on `Mr.`, `vs.`, `Jan.`, etc.

### Hierarchy Resolution — `HierarchyResolver`

Post-processing pass that assigns parent/sibling relationships to a flat chunk list:

- **Heading-path parentage** (markdown) — A chunk's parent is the nearest preceding chunk whose `heading_path` is a strict prefix.
- **Containment parentage** (code) — A chunk whose line range `[start_line, end_line]` is fully contained by another chunk's range is a child.
- **Sibling ordering** — Counts chunks per parent, assigns `sibling_order` (1-based ordinal) and `sibling_count` (total siblings under same parent).
- **Parent references** — Stores `parent_chunk_id` as `"chunk:{index}"` until UUIDs are assigned at insert time.

## Flow

```
File (path + content)
  │
  ▼
detect_file_type(file_path, content) → "code" | "markdown" | "text"
  │
  ▼
FileTypeDetector.get_chunker(file_type) → CodeChunker | MarkdownChunker | TextChunker
  │
  ▼
chunker.chunk(text, file_path=file_path) → list[Chunk]
  │
  ▼
HierarchyResolver.resolve(chunks) → list[Chunk] with parent_chunk_id, sibling_order
  │
  ▼
OllamaEmbedder.embed_batch(chunks) → chunks with vector populated
  │
  ▼
LanceDBStore.insert(chunks) → vector store + DuckDB sync
```

The `FileTypeDetector.chunk_file()` method combines detection and chunking into a single call. `ingest_tools.py` instantiates a `FileTypeDetector` and `HierarchyResolver`, calls `chunk_file()`, passes the result through `resolve()`, then embeds and stores.

## Integration

### Dependencies

- **tree-sitter** + language packages (`tree_sitter_python`, `tree_sitter_javascript`, etc.) — Required for `CodeChunker`. Dynamically imported per-language; raises `ImportError` with install instructions if missing.
- **ollama** Python package — Required for `TextChunker` semantic mode. Calls `ollama.embed()` for sentence embeddings. Graceful degradation: returns `None` on connection failure, triggering paragraph-mode fallback.
- **pyyaml** — Optional for `MarkdownChunker` frontmatter parsing. Falls back to storing raw frontmatter string if unavailable.
- **utils.models.Chunk** — Shared dataclass defining the chunk schema. All chunkers produce `Chunk` instances.

### Consumers

- **`src/tools/ingest_tools.py`** — Primary consumer. Imports `FileTypeDetector`, `detect_file_type`, `HierarchyResolver`, `CODE_EXTENSIONS`, `MARKDOWN_EXTENSIONS`. Uses them in `ingest_file()`, `ingest_text()`, and `ingest_directory()` to detect file types, route to chunkers, resolve hierarchy, then pass chunks to the storage layer.
- **`src/rag/embedder.py`** — Receives `Chunk` objects from the chunking pipeline and populates the `vector` field via Ollama embeddings.
- **`src/storage/lancedb_store.py`** — Stores `Chunk` objects (via `Chunk.to_lance()`) into LanceDB for vector search and full-text indexing.

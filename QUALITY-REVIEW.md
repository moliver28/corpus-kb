# Quality Review: corpus-kb Codebase

**Reviewer:** quality-reviewer (ultrawork-squad)
**Date:** 2026-06-18
**Scope:** All 18 source files in `src/`

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| Critical | 4 | Bugs, security issues, or correctness problems |
| Important | 12 | Significant code smells, missing comments, or over-engineering |
| Nice-to-have | 8 | Minor improvements, style consistency |

---

## File-by-File Findings

---

### 1. `src/server.py` (335 lines)

#### Important

**L117 — Silent exception swallowing with bare `pass`**
```python
except Exception:
    pass  # Non-blocking — first-run edge case
```
The comment explains intent but a bare `except Exception: pass` hides real errors (e.g., schema migration failures, connection errors). At minimum, log the exception.

**Severity:** Important

**L64-69 — Imports inside function body**
```python
from storage.lancedb_store import LanceDBStore
from storage.duckdb_engine import DuckDBEngine
...
```
These are inside `create_server()`. While this avoids circular imports, it makes the dependency graph opaque. Consider a comment explaining *why* these are deferred, or restructure to avoid the need.

**Severity:** Nice-to-have

**L199-201 — SQL injection risk in doc_resource**
```python
chunks = store.chunks_table.search().where(
    f"doc_id = '{doc_id}'"
).limit(10000).to_list()
```
The `doc_id` is interpolated directly into a WHERE clause. While `doc_id` comes from a resource URL pattern (not user input directly), this is still a SQL injection vector if the MCP client passes crafted IDs.

**Severity:** Critical

**L72 vs L108 — Inconsistent default dimensions**
```python
# L72
embed_dim = embedding_config.get("dimensions", 4096)
# L108
dimensions=embedding_config.get("dimensions", 768),
```
Two different defaults (4096 vs 768) for the same config key in the same function. This is a bug — the LanceDBStore and OllamaEmbedder will disagree on vector dimensions if no config is provided.

**Severity:** Critical

---

### 2. `src/storage/lancedb_store.py` (396 lines)

#### Critical

**L119, L127-128, L145, L161 — SQL injection via string interpolation**
```python
results = self.documents_table.search().where(f"doc_id = '{doc_id}'").to_list()
self.documents_table.delete(f"doc_id = '{doc_id}'")
self.chunks_table.delete(f"doc_id = '{doc_id}'")
```
All `doc_id` and `chunk_id` values are interpolated directly into SQL WHERE clauses. If any of these IDs come from external input (MCP tools), this is a SQL injection vulnerability. LanceDB's `.where()` accepts parameterized queries — use them.

**Severity:** Critical

#### Important

**L57 — Fragile table listing**
```python
existing = self.db.list_tables() if hasattr(self.db, "list_tables") else self.db.table_names()
```
This API compatibility shim suggests the code was written against multiple LanceDB versions. Add a comment noting which versions are supported, or pin the dependency.

**Severity:** Nice-to-have

**L242-254 — Overly complex tag resolution logic**
```python
tags_map = tbl.tags.list() if hasattr(tbl, "tags") else {}
...
if not tag and isinstance(tags_map, dict):
    for tname, tinfo in tags_map.items():
        if isinstance(tinfo, dict) and tinfo.get("version") == ver:
            tag = tname
            break
```
This double-lookup (direct key then linear scan) is confusing. The comment doesn't explain *why* two lookup strategies are needed. This appears to handle different LanceDB API versions for tag storage. Needs explanation.

**Severity:** Important

**L305-308 — Inefficient stats counting**
```python
total_docs = len(self.documents_table.search().limit(10000).to_list())
total_chunks = len(self.chunks_table.search().limit(10000).to_list())
```
Fetching up to 10,000 rows just to count them is wasteful. LanceDB likely supports `count_rows()` or similar. This will be slow on large datasets.

**Severity:** Important

**L368-395 — Duplicate RRF implementation**
`_rrf_fuse` is a static method here, but `hybrid_search.py` also has `_rrf_merge`. These are near-identical implementations of Reciprocal Rank Fusion. Consolidate into a single utility.

**Severity:** Important

---

### 3. `src/storage/duckdb_engine.py` (418 lines)

#### Important

**L251 — Wrong method name for affected rows**
```python
affected = self.conn.fetchreq("SELECT changes()").fetchone()[0]
```
`fetchreq` is not a standard DuckDB method. This should be `self.conn.execute(...)`. This is likely a typo that would cause a runtime error on write operations.

**Severity:** Critical

**L267-296 — `execute_safe` has no actual safety**
```python
def execute_safe(self, sql: str, params: Optional[list] = None) -> dict:
    ...
    for dangerous in ["DROP", "ALTER", "CREATE"]:
        if sql_stripped.startswith(dangerous) and "TABLE IF NOT EXISTS" not in sql_stripped:
            if dangerous not in ("CREATE",) or "TABLE" not in sql_stripped:
                pass  # Allow CREATE TABLE IF NOT EXISTS
```
The safety check does nothing — it just `pass`es. No error is returned, no blocking occurs. This method is functionally identical to `execute()` but with a misleading name. Either implement the safety or remove the method.

**Severity:** Important

**L221-244 — Safety check is case-sensitive and naive**
```python
sql_stripped = sql.strip().upper()
for dangerous in ["DROP TABLE", "DROP DATABASE", "DROP SCHEMA"]:
    if dangerous in sql_stripped:
```
This catches `DROP TABLE` in comments or string literals too. A CTE like `WITH drop_table_info AS (...) SELECT ...` would be falsely blocked. Conversely, `drop table` with a newline between words would slip through. For an MCP tool exposed to users, this is acceptable as a first line of defense but should be documented as "best effort."

**Severity:** Nice-to-have

**L169 — Hard-coded row limit**
```python
chunks_data = lancedb_store.chunks_table.search().limit(100000).to_list()
```
100,000 rows loaded into memory at once. For large codebases this will be slow and memory-intensive. Consider streaming or batched sync.

**Severity:** Important

---

### 4. `src/storage/graph_store.py` (422 lines)

#### Important

**L360, L373-374, L383-384, L393-395 — SQL injection in GraphQLite queries**
```python
f"MATCH (n) WHERE n.id = '{entity_id}' RETURN n"
f"MATCH (n)-[r]-(m) WHERE n.id = '{entity_id}' RETURN ..."
f"MATCH (n) WHERE n.name CONTAINS '{query}'{type_filter} RETURN ..."
```
All GraphQLite queries use f-string interpolation. If `entity_id` or `query` comes from user input, this is injection. Use parameterized queries if GraphQLite supports them.

**Severity:** Important

**L119 — `executescript` auto-commits, bypassing WAL mode**
```python
self.conn.executescript("""...""")
self.conn.commit()  # This is redundant — executescript already commits
```
`executescript()` issues an implicit COMMIT before running. The explicit `self.conn.commit()` on L145 is redundant. Also, `executescript` resets the journal mode, potentially undoing the WAL setting from L114.

**Severity:** Important

**L332-421 — Dynamically defined class inside a function**
The `GraphQLiteGraphStore` class is defined inside `_create_graphqlite_store()`. This is a valid pattern for optional dependencies but makes debugging harder (class name shows as `graph_store._create_graphqlite_store.<locals>.GraphQLiteGraphStore`). Consider moving to a separate module.

**Severity:** Nice-to-have

**L218 — Redundant parameter passing**
```python
""", (entity_id, entity_id, entity_id, entity_id)).fetchall()
```
The same `entity_id` is passed 4 times. This is correct for the SQL but looks like a copy-paste error. A comment explaining the 4 uses would help.

**Severity:** Nice-to-have

---

### 5. `src/rag/embedder.py` (119 lines)

#### Important

**L61-62 — Cache eviction clears everything**
```python
if len(self._cache) >= self._cache_max:
    self._cache.clear()
```
This is not LRU — it's "clear all when full." A true LRU would evict only the oldest entry. This means a cache at 9,999/10,000 entries that gets one new item loses all 9,999 cached embeddings. Use `collections.OrderedDict` with `move_to_end()` or `functools.lru_cache`.

**Severity:** Important

**L56-58 — Silent failure returns zero vectors**
```python
except Exception:
    vector = [0.0] * self.dimensions
```
No logging, no warning. If Ollama is down, every embedding silently becomes a zero vector, and the user gets meaningless search results. At minimum, log a warning on first failure.

**Severity:** Important

---

### 6. `src/rag/hybrid_search.py` (147 lines)

#### Nice-to-have

**L126 — Import inside method**
```python
from collections import OrderedDict
```
This should be a top-level import. No reason to defer it.

**Severity:** Nice-to-have

**L128 — OrderedDict is unnecessary**
Python 3.7+ dicts maintain insertion order. `OrderedDict` adds no value here.

**Severity:** Nice-to-have

---

### 7. `src/rag/reranker.py` (117 lines)

#### Important

**L81 — Wrong model for ranking**
```python
model="nomic-embed-text",  # lightweight — swap for llama3.2 if available
```
`nomic-embed-text` is an *embedding* model, not a text generation model. `ollama.generate()` on an embedding model will either fail or produce garbage. The comment acknowledges this should be swapped, but the default is broken.

**Severity:** Important

**L87 — Inefficient membership check**
```python
reranked.extend(r for r in candidates if r not in reranked)
```
`r not in reranked` does object identity comparison on SearchResult dataclass instances. This works but is O(n²). Use a set of chunk_ids for O(n) lookup.

**Severity:** Nice-to-have

---

### 8. `src/chunking/code_chunker.py` (540 lines)

#### Important

**File exceeds 250-line soft limit** (540 lines). Should be split — e.g., entity extraction logic into a separate module.

**Severity:** Important

**L104, L106 — Use of `any` type**
```python
self._parsers: dict[str, any] = {}
def _get_parser(self, lang_name: str) -> any:
```
Should be `Any` from `typing` (capitalized). Also, the actual type is `tree_sitter.Parser` — use that.

**Severity:** Nice-to-have

**L227-342 — Massive language-specific entity collection logic**
The `_collect_entities` method has a giant if/elif chain for each language (Python, JS/TS, Rust, Go, Java). This is 115 lines of near-identical pattern matching. Consider using the `ENTITY_QUERIES` dict (L41-79) with tree-sitter's `Query` API instead of manual AST traversal. The queries are defined but never used.

**Severity:** Important

**L400-411 — Duplicate `"func "` in boundary detection**
```python
or line.strip().startswith("func ")
or line.strip().startswith("func ")  # duplicate
```
`"func "` appears twice (L406 and L407). Harmless but indicates copy-paste.

**Severity:** Nice-to-have

**L369-384 — `_build_scope_chain` only handles Python/JS node types**
```python
if parent.type in ("class_definition", "function_definition", "module", "program"):
```
This doesn't account for Rust (`struct_item`, `impl_item`), Go (`type_declaration`), or Java (`class_declaration`, `method_declaration`). Scope chains will be incomplete for non-Python/JS code.

**Severity:** Important

---

### 9. `src/chunking/markdown_chunker.py` (231 lines)

#### Nice-to-have

**L175-176 — `flush_buffer` uses `nonlocal` correctly but is hard to follow**
The nested function with `nonlocal` is fine but could be simplified by using a list-as-accumulator pattern. Not a bug, just slightly harder to read than necessary.

**Severity:** Nice-to-have

**L20 — Heading regex doesn't handle headings inside code blocks**
```python
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
```
This will match `# heading` inside fenced code blocks. The docstring says it won't split inside code blocks, but the regex doesn't enforce this. The current implementation works because `_find_sections` is called on the body *after* frontmatter extraction, but code blocks within sections are not protected.

**Severity:** Important

---

### 10. `src/chunking/text_chunker.py` (283 lines)

#### Important

**L144 — Naive sentence splitting**
```python
words = text.replace("\n", " ").split(" ")
```
Splitting on single spaces breaks on multiple spaces, tabs, or non-ASCII whitespace. Also, this approach is O(n) in words but creates many intermediate strings. Consider using a proper sentence tokenizer or at least `re.split(r'(?<=[.!?])\s+', text)`.

**Severity:** Important

**L188-221 — Nested function definitions**
`cosine_sim` and `window_embedding` are defined inside `_compute_gaps`. These are pure functions that could be module-level. Defining them inside means they're recreated on every call.

**Severity:** Nice-to-have

---

### 11. `src/chunking/detector.py` (155 lines)

#### Nice-to-have

**L67-90 and L93-107 — Duplicated logic**
`detect_file_type` and `detect_language` share almost identical code. `detect_file_type` could call `detect_language` and return `"code"` if a language is found.

**Severity:** Nice-to-have

---

### 12. `src/chunking/hierarchy.py` (144 lines)

#### Nice-to-have

**L72 — Magic value `-1` for "no parent"**
```python
parent_map[idx_a] = -1
```
Using `-1` as a sentinel is fine but should be documented or replaced with `None`. The code at L88-90 handles it correctly but the intent isn't obvious.

**Severity:** Nice-to-have

---

### 13. `src/tools/ingest_tools.py` (235 lines)

#### Important

**L86-87, L142-143 — Silent exception swallowing**
```python
except Exception:
    pass  # Non-blocking
```
Same pattern as `server.py`. If the DuckDB sync fails, the user never knows. At minimum, log a warning.

**Severity:** Important

**L24-95 and L98-150 — Duplicated ingest logic**
`_ingest_single_file` and `_ingest_text` share ~80% identical code (chunk → resolve → embed → store → sync). Extract a common `_store_chunks` helper.

**Severity:** Important

---

### 14. `src/tools/search_tools.py` (164 lines)

#### Nice-to-have

**L26-27 — Hard-coded reranker mode**
```python
reranker = Reranker(mode="identity")
```
The reranker mode is hard-coded. If someone wants LLM-based reranking, they'd need to modify this file. Should be configurable.

**Severity:** Nice-to-have

---

### 15. `src/tools/graph_tools.py` (142 lines)

#### Important

**L129-141 — `get_entity_relations` iterates over wrong structure**
```python
for n in neighbors:
    for rel in n.get("relations", []):
```
Looking at `SQLiteGraphStore.get_neighbors()` (graph_store.py L208-234), the return structure is `{"entity": Entity, "relation": {...}}`, not `{"relations": [...]}`. The `n.get("relations", [])` will always return `[]`, meaning this tool **always returns an empty list**. This is a bug.

**Severity:** Critical

---

### 16. `src/tools/database_tools.py` (190 lines)

#### Nice-to-have

**L62 — `sql_query` doesn't use the `limit` parameter for the engine call**
```python
result = engine.execute(query)
if result.get("rows") and not result.get("error"):
    result["rows"] = result["rows"][:min(limit, 5000)]
```
The limit is applied *after* the full query executes. If the user writes `SELECT * FROM chunks` with millions of rows, all rows are fetched into memory before truncation. The limit should be injected into the query or the engine should support it.

**Severity:** Important

---

### 17. `src/tools/version_tools.py` (113 lines)

#### Nice-to-have

**L71, L80, L89, L98, L107 — Async tools with no async operations**
These tools are declared `async def` but call synchronous methods (`store.checkout()`, `store.restore()`, etc.). This adds no value and may confuse the MCP framework. Either make the underlying store methods async or remove `async` from the tools.

**Severity:** Nice-to-have

**L64-65 — Wrong key names for graph stats**
```python
"total_entities": graph_stats.get("entity_count", 0),
"total_relations": graph_stats.get("relation_count", 0),
```
Looking at `SQLiteGraphStore.get_stats()` (graph_store.py L278-290), the keys are `"total_entities"` and `"total_relations"`, not `"entity_count"` and `"relation_count"`. This means these will **always return 0**.

**Severity:** Critical

---

### 18. `src/utils/models.py` (152 lines)

#### Nice-to-have

**L11-12 — `datetime.utcnow()` is deprecated**
```python
def _now() -> str:
    return datetime.utcnow().isoformat()
```
`datetime.utcnow()` is deprecated in Python 3.12+. Use `datetime.now(timezone.utc)` instead.

**Severity:** Nice-to-have

**L34 — Mutable default in dataclass**
```python
metadata: dict = field(default_factory=dict)
```
This is correct (uses `default_factory`), but the same pattern is used for `heading_path` and `scope_chain` which are `list[str]`. All are correct — no issue here, just noting the pattern is consistent.

---

## Cross-Cutting Issues

### 1. SQL Injection (Critical — Multiple Files)
String interpolation in WHERE clauses across `lancedb_store.py` (L119, L127-128, L145, L161), `server.py` (L199-201), and `graph_store.py` GraphQLite queries (L360, L373-374, L383-384, L393-395). All IDs and query strings should use parameterized queries.

### 2. Silent Exception Swallowing (Important — Multiple Files)
`server.py` L117, `ingest_tools.py` L86-87/L142-143. Bare `except Exception: pass` hides real errors. Replace with logging.

### 3. Duplicated RRF Implementation (Important)
`lancedb_store.py` `_rrf_fuse` (L368-395) and `hybrid_search.py` `_rrf_merge` (L116-147) are near-identical. Consolidate.

### 4. Duplicated Ingest Logic (Important)
`_ingest_single_file` and `_ingest_text` in `ingest_tools.py` share ~80% code. Extract common helper.

### 5. Inconsistent Config Defaults (Critical)
`server.py` L72 uses `dimensions=4096` default while L108 uses `dimensions=768`. One of these is wrong.

### 6. Broken Tool Returns (Critical)
- `get_entity_relations` in `graph_tools.py` always returns `[]` due to wrong key name.
- `get_stats` in `version_tools.py` always returns 0 for entities/relations due to wrong key names.

### 7. `execute_safe` Does Nothing (Important)
`duckdb_engine.py` L267-296: The method name implies safety but the safety logic is a no-op.

### 8. `fetchreq` Typo (Critical)
`duckdb_engine.py` L251: `self.conn.fetchreq(...)` should be `self.conn.execute(...)`.

---

## Recommendations Priority Order

1. **Fix `fetchreq` typo** in `duckdb_engine.py` L251 — write operations are broken
2. **Fix `get_entity_relations`** in `graph_tools.py` — tool always returns empty
3. **Fix graph stats key names** in `version_tools.py` L64-65 — always returns 0
4. **Fix dimension mismatch** in `server.py` L72 vs L108
5. **Add parameterized queries** to all SQL WHERE clauses
6. **Replace bare `except: pass`** with logging
7. **Consolidate duplicate RRF** implementations
8. **Extract common ingest helper** in `ingest_tools.py`
9. **Fix `execute_safe`** to actually enforce safety or remove it
10. **Fix reranker default model** in `reranker.py` L81

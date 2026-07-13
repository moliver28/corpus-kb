# W1 & W2 Quick Reference: Exact Line Edits

**For:** Junior developers executing the decomposition  
**Format:** Copy-paste ready edits  
**Time:** ~2.5 hours (dev) + 1 hour (review)

---

## File Creation Checklist

### W1.1: `src/migrations/001_create_schema.sql` ✓ NEW

**Action:** Create new file with full SQL DDL from WAVE-1-2-DECOMPOSITION.md (W1.1 section)

**Verify:**
```bash
psql corpus-kb -c "\dt" | grep events
# Output: public | events | table | postgres
```

---

### W1.2: `src/domain/application.py` ✓ NEW

**Action:** Create new file with full Python code from WAVE-1-2-DECOMPOSITION.md (W1.2 section)

**Verify:**
```bash
python -c "from src.domain.application import get_app; print('OK')"
# Output: OK
```

---

### W1.3: `src/migrations/002_enable_rls.sql` ✓ NEW

**Action:** Create new file with full SQL DDL from WAVE-1-2-DECOMPOSITION.md (W1.3 section)

**Verify:**
```bash
psql corpus-kb -c "\d documents" | grep -i "policies"
# Output: Policies: documents_tenant_isolation
```

---

### W2.1: `src/domain/aggregates.py` ✓ NEW

**Action:** Create new file with full Python code from WAVE-1-2-DECOMPOSITION.md (W2.1 section)

**Verify:**
```bash
python -c "from src.domain.aggregates import Document, Entity, Relation; print('OK')"
# Output: OK
```

---

### W2.2: `src/domain/models.py` ✓ NEW

**Action:** Create new file with full Python code from WAVE-1-2-DECOMPOSITION.md (W2.2 section)

**Verify:**
```bash
python -c "from src.domain.models import IngestFileCommand, SearchQuery; print('OK')"
# Output: OK
```

---

## Dependency Edits

### MOD: `pyproject.toml`

**Location:** Lines 13-29 (dependencies section)

**OLD:**
```toml
dependencies = [
    "mcp[cli]>=1.0",
    "lancedb>=0.12",
    "pyarrow>=12.0",
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
    "pydantic>=2.0",
    "rich>=13.0",
]
```

**NEW:**
```toml
dependencies = [
    "mcp[cli]>=1.0",
    "lancedb>=0.12",
    "pyarrow>=12.0",
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
    "pydantic>=2.0",
    "rich>=13.0",
    "eventsourcing>=10.0",
    "sqlalchemy>=2.0",
    "starlette>=0.35",
    "uvicorn>=0.24",
    "psycopg2-binary>=2.9",
]
```

**Reason:** W1.2 (application.py) needs eventsourcing. W3.2 (query_handler.py) needs sqlalchemy. W5.1 (http.py) needs starlette + uvicorn. W3.2 needs psycopg2 for Postgres connection.

---

### MOD: `config.yaml`

**Location:** Add new section after `storage:` section

**OLD:**
```yaml
storage:
  path: ~/.corpus-kb
  lancedb_uri: ~/.corpus-kb/lancedb
  graph_db: ~/.corpus-kb/graph.db
```

**NEW:**
```yaml
storage:
  path: ~/.corpus-kb
  lancedb_uri: ~/.corpus-kb/lancedb
  graph_db: ~/.corpus-kb/graph.db

database:
  connection_string: "postgresql://user:password@localhost:5432/corpus-kb"
  echo: false  # Set to true for SQL debugging
```

**Reason:** W1.2 (application.py) reads `config["database"]["connection_string"]`. W3.2 (query_handler.py) uses it to connect to Postgres.

---

## Directory Structure After W1 + W2

```
src/
├── domain/                    # NEW
│   ├── __init__.py           # NEW (empty)
│   ├── application.py        # NEW (W1.2)
│   ├── aggregates.py         # NEW (W2.1)
│   └── models.py             # NEW (W2.2)
├── migrations/               # NEW
│   ├── 001_create_schema.sql # NEW (W1.1)
│   └── 002_enable_rls.sql    # NEW (W1.3)
├── chunking/                 # EXISTING
├── storage/                  # EXISTING
├── rag/                       # EXISTING
├── tools/                     # EXISTING
├── utils/                     # EXISTING
├── config.py                 # EXISTING
└── validators.py             # EXISTING
```

---

## Execution Order (Sequential)

### Phase 1: Database Setup (5 min)

1. **Create schema:**
   ```bash
   psql corpus-kb < src/migrations/001_create_schema.sql
   ```
   
2. **Enable RLS:**
   ```bash
   psql corpus-kb < src/migrations/002_enable_rls.sql
   ```

3. **Verify:**
   ```bash
   psql corpus-kb -c "\dt" | grep -E "events|documents|chunks|entities|relations"
   # Should list 6 tables
   ```

### Phase 2: Python Setup (10 min)

1. **Update dependencies:**
   ```bash
   # Edit pyproject.toml (add eventsourcing, sqlalchemy, starlette, uvicorn, psycopg2-binary)
   pip install -e .
   ```

2. **Update config:**
   ```bash
   # Edit config.yaml (add database.connection_string)
   ```

3. **Create domain module:**
   ```bash
   mkdir -p src/domain
   touch src/domain/__init__.py
   ```

### Phase 3: Create Files (15 min)

1. **Create application.py:**
   ```bash
   # Copy full content from WAVE-1-2-DECOMPOSITION.md (W1.2 section)
   cat > src/domain/application.py << 'EOF'
   [paste full content]
   EOF
   ```

2. **Create aggregates.py:**
   ```bash
   # Copy full content from WAVE-1-2-DECOMPOSITION.md (W2.1 section)
   cat > src/domain/aggregates.py << 'EOF'
   [paste full content]
   EOF
   ```

3. **Create models.py:**
   ```bash
   # Copy full content from WAVE-1-2-DECOMPOSITION.md (W2.2 section)
   cat > src/domain/models.py << 'EOF'
   [paste full content]
   EOF
   ```

### Phase 4: Verify (10 min)

```bash
# Test imports
python -c "from src.domain.application import get_app; print('✓ application.py')"
python -c "from src.domain.aggregates import Document, Entity, Relation; print('✓ aggregates.py')"
python -c "from src.domain.models import IngestFileCommand, SearchQuery; print('✓ models.py')"

# Test database
psql corpus-kb -c "SELECT COUNT(*) FROM events;" # Should return 0
psql corpus-kb -c "SELECT COUNT(*) FROM documents;" # Should return 0

# Test RLS
psql corpus-kb -c "\d documents" | grep -i "policies"
```

---

## Common Issues & Fixes

### Issue: `psql: command not found`

**Fix:** Install Postgres client
```bash
# macOS
brew install postgresql

# Ubuntu/Debian
sudo apt-get install postgresql-client

# Windows
# Download from https://www.postgresql.org/download/windows/
```

---

### Issue: `FATAL: database "corpus-kb" does not exist`

**Fix:** Create database first
```bash
psql -U postgres -c "CREATE DATABASE \"corpus-kb\";"
```

---

### Issue: `ImportError: No module named 'eventsourcing'`

**Fix:** Install dependencies
```bash
pip install -e .
```

---

### Issue: `ModuleNotFoundError: No module named 'src'`

**Fix:** Add `src/` to PYTHONPATH
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python -c "from src.domain.application import get_app; print('OK')"
```

Or run from project root:
```bash
cd /path/to/corpus-kb
python -c "from src.domain.application import get_app; print('OK')"
```

---

### Issue: `psycopg2.OperationalError: could not connect to server`

**Fix:** Ensure Postgres is running
```bash
# macOS
brew services start postgresql

# Ubuntu/Debian
sudo systemctl start postgresql

# Windows
# Start PostgreSQL service from Services app
```

---

### Issue: `eventsourcing.exceptions.OperationalError: could not connect to database`

**Fix:** Check connection string in config.yaml
```yaml
database:
  connection_string: "postgresql://user:password@localhost:5432/corpus-kb"
```

Replace `user` and `password` with actual Postgres credentials.

---

## Testing Commands (Copy-Paste Ready)

### Test W1.1 (Schema)
```bash
psql corpus-kb -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
"
# Should list: chunks, documents, entities, events, projection_checkpoints, relations, tenants
```

### Test W1.2 (Application)
```bash
python << 'EOF'
from src.domain.application import get_app
app = get_app()
print(f"✓ Application initialized: {app}")
print(f"✓ Config loaded: {app.config}")
EOF
```

### Test W1.3 (RLS)
```bash
psql corpus-kb -c "
SELECT schemaname, tablename, policyname
FROM pg_policies
WHERE tablename IN ('documents', 'chunks', 'entities', 'relations', 'events')
ORDER BY tablename;
"
# Should list 5 policies (one per table)
```

### Test W2.1 (Aggregates)
```bash
python << 'EOF'
from src.domain.aggregates import Document, Entity, Relation
from uuid import uuid4

tenant_id = uuid4()

# Test Document
doc = Document(tenant_id=tenant_id, source="test.py")
print(f"✓ Document created: {doc.id}")

# Test Entity
entity = Entity(tenant_id=tenant_id, name="test_function", entity_type="Function")
print(f"✓ Entity created: {entity.id}")

# Test Relation
relation = Relation(
    tenant_id=tenant_id,
    source_entity_id=uuid4(),
    target_entity_id=uuid4(),
    relation_type="CALLS"
)
print(f"✓ Relation created: {relation.id}")
EOF
```

### Test W2.2 (Models)
```bash
python << 'EOF'
from src.domain.models import (
    IngestFileCommand, SearchQuery, AddEntityCommand,
    DocumentIngestedEvent, EntityAddedEvent
)
from uuid import uuid4
import json

tenant_id = uuid4()

# Test command serialization
cmd = IngestFileCommand(tenant_id=tenant_id, file_path="test.py")
cmd_json = cmd.model_dump_json()
print(f"✓ IngestFileCommand serialized: {len(cmd_json)} bytes")

# Test query serialization
query = SearchQuery(tenant_id=tenant_id, query="test", k=10)
query_json = query.model_dump_json()
print(f"✓ SearchQuery serialized: {len(query_json)} bytes")

# Test event serialization
event = DocumentIngestedEvent(
    tenant_id=tenant_id,
    aggregate_id=uuid4(),
    payload={"source": "test.py", "source_type": "file"}
)
event_json = event.model_dump_json()
print(f"✓ DocumentIngestedEvent serialized: {len(event_json)} bytes")
EOF
```

---

## Review Checklist

### Code Quality
- [ ] All files follow PEP 8 (4-space indentation, max 100 chars per line)
- [ ] All functions have docstrings
- [ ] All classes have docstrings
- [ ] No `print()` statements (use `logging` instead)
- [ ] No hardcoded values (use config)
- [ ] No `TODO` comments without context

### Architecture
- [ ] Aggregates use `@event` decorator (eventsourcing pattern)
- [ ] Commands include `tenant_id` (multi-tenancy)
- [ ] Events are immutable (use `@dataclass`)
- [ ] Models are JSON-serializable (Pydantic)
- [ ] Application is singleton (get_app pattern)

### Security
- [ ] No hardcoded passwords in code
- [ ] Connection string from config (not hardcoded)
- [ ] RLS policies enabled on all tables
- [ ] tenant_id is immutable in aggregates

### Testing
- [ ] Schema creation verified (tables exist)
- [ ] RLS policies verified (policies exist)
- [ ] Application initialization verified (get_app works)
- [ ] Aggregates instantiate correctly
- [ ] Models serialize to JSON

### Integration
- [ ] Dependencies added to pyproject.toml
- [ ] Config updated with database connection string
- [ ] Directory structure created (src/domain/, src/migrations/)
- [ ] All imports resolve (no ImportError)

---

## Next Steps (After W1 + W2)

Once W1 + W2 are complete and verified:

1. **W3.1:** Create CommandHandler (uses W2.1 + W2.2)
2. **W3.2:** Create QueryHandler (uses W2.2)
3. **W4.1:** Create Postgres Projections (uses W3.1 + W3.2)
4. **W4.2:** Create LanceDB Projections (uses W3.1 + W3.2)
5. **W5.1:** Create HTTP Adapter (uses W4.1 + W4.2)
6. **W5.2:** Create Socket Adapter (uses W4.1 + W4.2)
7. **W5.3:** Refactor MCP Adapter (uses W4.1 + W4.2)

---

## Time Estimates

| Task | Dev | Review | Total |
|------|-----|--------|-------|
| W1.1 (Schema) | 20 min | 15 min | 35 min |
| W1.2 (Application) | 15 min | 15 min | 30 min |
| W1.3 (RLS) | 20 min | 20 min | 40 min |
| W2.1 (Aggregates) | 25 min | 15 min | 40 min |
| W2.2 (Models) | 20 min | 15 min | 35 min |
| **TOTAL** | **100 min** | **80 min** | **180 min** |

**Parallel execution possible:** All W1 tasks can run in parallel (no code dependencies). All W2 tasks can run in parallel (both depend on W1).

**Realistic timeline:** 2.5-3 hours for one developer (sequential), 1.5-2 hours for two developers (parallel W1 + W2).


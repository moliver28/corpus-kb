# W1 & W2 Execution Guide: Step-by-Step for Junior Developers

**Audience:** Junior developer with Python 3.11+ experience  
**Goal:** Execute WAVE 1 (Foundation) + WAVE 2 (Domain Model) with zero ambiguity  
**Time:** 2.5-3 hours (sequential), 1.5-2 hours (parallel with pair)  
**Success Criteria:** All tests pass, all imports resolve, database schema created

---

## Pre-Execution Checklist

Before starting, verify you have:

- [ ] Python 3.11+ installed: `python --version`
- [ ] Postgres 13+ installed: `psql --version`
- [ ] Git repo cloned: `cd /path/to/corpus-kb`
- [ ] Virtual environment active: `source venv/bin/activate` (or `.venv\Scripts\activate` on Windows)
- [ ] Postgres running: `psql -U postgres -c "SELECT 1;"`
- [ ] Database created: `psql -U postgres -c "CREATE DATABASE \"corpus-kb\";" 2>/dev/null || true`

---

## PHASE 1: Database Setup (5 minutes)

### Step 1.1: Create Schema

**File:** `src/migrations/001_create_schema.sql`

**Action:**
1. Open `WAVE-1-2-DECOMPOSITION.md`
2. Find section **W1.1: Create Postgres Schema**
3. Copy the entire SQL content (from `-- src/migrations/001_create_schema.sql` to the last `CREATE INDEX`)
4. Create file: `src/migrations/001_create_schema.sql`
5. Paste content
6. Save

**Verify:**
```bash
psql corpus-kb < src/migrations/001_create_schema.sql
# Output should show: CREATE TABLE, CREATE INDEX (no errors)

psql corpus-kb -c "\dt"
# Output should list: chunks, documents, entities, events, projection_checkpoints, relations, tenants
```

**If error:** Check Postgres is running and database exists
```bash
psql -U postgres -c "CREATE DATABASE \"corpus-kb\";" 2>/dev/null || true
```

---

### Step 1.2: Enable RLS

**File:** `src/migrations/002_enable_rls.sql`

**Action:**
1. Open `WAVE-1-2-DECOMPOSITION.md`
2. Find section **W1.3: Design RLS Policies**
3. Copy the entire SQL content (from `-- src/migrations/002_enable_rls.sql` to the last `GRANT`)
4. Create file: `src/migrations/002_enable_rls.sql`
5. Paste content
6. Save

**Verify:**
```bash
psql corpus-kb < src/migrations/002_enable_rls.sql
# Output should show: ALTER TABLE, CREATE POLICY, CREATE ROLE (no errors)

psql corpus-kb -c "\d documents" | grep -i "policies"
# Output should show: Policies: documents_tenant_isolation
```

---

## PHASE 2: Python Dependencies (5 minutes)

### Step 2.1: Update pyproject.toml

**File:** `pyproject.toml`

**Action:**
1. Open `pyproject.toml`
2. Find the `dependencies = [` section (around line 13)
3. Locate the closing `]` (around line 29)
4. Add these 5 lines BEFORE the closing `]`:
   ```toml
   "eventsourcing>=10.0",
   "sqlalchemy>=2.0",
   "starlette>=0.35",
   "uvicorn>=0.24",
   "psycopg2-binary>=2.9",
   ```
5. Save

**Verify:**
```bash
cat pyproject.toml | grep -A 20 "dependencies = \["
# Should show the 5 new dependencies
```

---

### Step 2.2: Update config.yaml

**File:** `config.yaml`

**Action:**
1. Open `config.yaml`
2. Find the `storage:` section (around line 10)
3. After the `storage:` section, add:
   ```yaml
   
   database:
     connection_string: "postgresql://postgres:postgres@localhost:5432/corpus-kb"
     echo: false
   ```
4. Save

**Note:** Replace `postgres:postgres` with your actual Postgres username:password if different.

**Verify:**
```bash
grep -A 2 "^database:" config.yaml
# Should show: database:, connection_string:, echo:
```

---

### Step 2.3: Install Dependencies

**Action:**
```bash
pip install -e .
```

**Verify:**
```bash
python -c "import eventsourcing; import sqlalchemy; import starlette; print('✓ All dependencies installed')"
# Output: ✓ All dependencies installed
```

---

## PHASE 3: Create Domain Module (15 minutes)

### Step 3.1: Create Directory

**Action:**
```bash
mkdir -p src/domain
touch src/domain/__init__.py
```

**Verify:**
```bash
ls -la src/domain/
# Should show: __init__.py
```

---

### Step 3.2: Create application.py

**File:** `src/domain/application.py`

**Action:**
1. Open `WAVE-1-2-DECOMPOSITION.md`
2. Find section **W1.2: Create Eventsourcing Application**
3. Copy the entire Python content (from `# src/domain/application.py` to the last line)
4. Create file: `src/domain/application.py`
5. Paste content
6. Save

**Verify:**
```bash
python -c "from src.domain.application import get_app; print('✓ application.py imports successfully')"
# Output: ✓ application.py imports successfully
```

---

### Step 3.3: Create aggregates.py

**File:** `src/domain/aggregates.py`

**Action:**
1. Open `WAVE-1-2-DECOMPOSITION.md`
2. Find section **W2.1: Define Domain Aggregates**
3. Copy the entire Python content (from `# src/domain/aggregates.py` to the last line)
4. Create file: `src/domain/aggregates.py`
5. Paste content
6. Save

**Verify:**
```bash
python -c "from src.domain.aggregates import Document, Entity, Relation; print('✓ aggregates.py imports successfully')"
# Output: ✓ aggregates.py imports successfully
```

---

### Step 3.4: Create models.py

**File:** `src/domain/models.py`

**Action:**
1. Open `WAVE-1-2-DECOMPOSITION.md`
2. Find section **W2.2: Create Pydantic Command/Event Models**
3. Copy the entire Python content (from `# src/domain/models.py` to the last line)
4. Create file: `src/domain/models.py`
5. Paste content
6. Save

**Verify:**
```bash
python -c "from src.domain.models import IngestFileCommand, SearchQuery; print('✓ models.py imports successfully')"
# Output: ✓ models.py imports successfully
```

---

## PHASE 4: Comprehensive Testing (10 minutes)

### Test 4.1: Database Schema

**Action:**
```bash
psql corpus-kb -c "
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
"
```

**Expected Output:**
```
     table_name      
---------------------
 chunks
 documents
 entities
 events
 projection_checkpoints
 relations
 tenants
(7 rows)
```

---

### Test 4.2: RLS Policies

**Action:**
```bash
psql corpus-kb -c "
SELECT schemaname, tablename, policyname
FROM pg_policies
WHERE tablename IN ('documents', 'chunks', 'entities', 'relations', 'events')
ORDER BY tablename;
"
```

**Expected Output:**
```
 schemaname | tablename | policyname
------------+-----------+----------------------------------
 public     | chunks    | chunks_tenant_isolation
 public     | documents | documents_tenant_isolation
 public     | entities  | entities_tenant_isolation
 public     | events    | events_tenant_isolation
 public     | relations | relations_tenant_isolation
(5 rows)
```

---

### Test 4.3: Python Imports

**Action:**
```bash
python << 'EOF'
print("Testing imports...")

from src.domain.application import get_app
print("✓ application.py")

from src.domain.aggregates import Document, Entity, Relation
print("✓ aggregates.py")

from src.domain.models import (
    IngestFileCommand, SearchQuery, AddEntityCommand,
    DocumentIngestedEvent, EntityAddedEvent
)
print("✓ models.py")

print("\nAll imports successful!")
EOF
```

**Expected Output:**
```
Testing imports...
✓ application.py
✓ aggregates.py
✓ models.py

All imports successful!
```

---

### Test 4.4: Application Initialization

**Action:**
```bash
python << 'EOF'
from src.domain.application import get_app

try:
    app = get_app()
    print(f"✓ Application initialized: {type(app).__name__}")
    print(f"✓ Config loaded: {bool(app.config)}")
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
EOF
```

**Expected Output:**
```
✓ Application initialized: CorpusApplication
✓ Config loaded: True
```

**If error:** Check config.yaml has `database.connection_string` and Postgres is running.

---

### Test 4.5: Aggregate Creation

**Action:**
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

print("\nAll aggregates created successfully!")
EOF
```

**Expected Output:**
```
✓ Document created: <uuid>
✓ Entity created: <uuid>
✓ Relation created: <uuid>

All aggregates created successfully!
```

---

### Test 4.6: Model Serialization

**Action:**
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

# Verify JSON is valid
json.loads(cmd_json)
json.loads(query_json)
json.loads(event_json)

print("\nAll models serialize to JSON successfully!")
EOF
```

**Expected Output:**
```
✓ IngestFileCommand serialized: XXX bytes
✓ SearchQuery serialized: XXX bytes
✓ DocumentIngestedEvent serialized: XXX bytes

All models serialize to JSON successfully!
```

---

## PHASE 5: Final Verification (5 minutes)

### Checklist

Run this final verification script:

```bash
python << 'EOF'
import sys

checks = []

# 1. Database schema
try:
    import psycopg2
    conn = psycopg2.connect("dbname=corpus-kb user=postgres")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';")
    count = cur.fetchone()[0]
    checks.append(("Database schema", count >= 7))
    conn.close()
except Exception as e:
    checks.append(("Database schema", False))
    print(f"  Error: {e}")

# 2. RLS policies
try:
    import psycopg2
    conn = psycopg2.connect("dbname=corpus-kb user=postgres")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pg_policies WHERE tablename IN ('documents', 'chunks', 'entities', 'relations', 'events');")
    count = cur.fetchone()[0]
    checks.append(("RLS policies", count >= 5))
    conn.close()
except Exception as e:
    checks.append(("RLS policies", False))
    print(f"  Error: {e}")

# 3. Python imports
try:
    from src.domain.application import get_app
    from src.domain.aggregates import Document, Entity, Relation
    from src.domain.models import IngestFileCommand, SearchQuery
    checks.append(("Python imports", True))
except Exception as e:
    checks.append(("Python imports", False))
    print(f"  Error: {e}")

# 4. Application initialization
try:
    from src.domain.application import get_app
    app = get_app()
    checks.append(("Application init", app is not None))
except Exception as e:
    checks.append(("Application init", False))
    print(f"  Error: {e}")

# 5. Aggregate creation
try:
    from src.domain.aggregates import Document, Entity, Relation
    from uuid import uuid4
    tenant_id = uuid4()
    doc = Document(tenant_id=tenant_id, source="test.py")
    entity = Entity(tenant_id=tenant_id, name="test", entity_type="Function")
    relation = Relation(tenant_id=tenant_id, source_entity_id=uuid4(), target_entity_id=uuid4())
    checks.append(("Aggregate creation", True))
except Exception as e:
    checks.append(("Aggregate creation", False))
    print(f"  Error: {e}")

# 6. Model serialization
try:
    from src.domain.models import IngestFileCommand, SearchQuery
    from uuid import uuid4
    import json
    tenant_id = uuid4()
    cmd = IngestFileCommand(tenant_id=tenant_id, file_path="test.py")
    query = SearchQuery(tenant_id=tenant_id, query="test")
    json.loads(cmd.model_dump_json())
    json.loads(query.model_dump_json())
    checks.append(("Model serialization", True))
except Exception as e:
    checks.append(("Model serialization", False))
    print(f"  Error: {e}")

# Print results
print("\n" + "="*50)
print("FINAL VERIFICATION RESULTS")
print("="*50)
for check_name, passed in checks:
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"{status:8} | {check_name}")

all_passed = all(passed for _, passed in checks)
print("="*50)
if all_passed:
    print("✓ ALL CHECKS PASSED - W1 + W2 COMPLETE")
    sys.exit(0)
else:
    print("✗ SOME CHECKS FAILED - SEE ABOVE")
    sys.exit(1)
EOF
```

**Expected Output:**
```
==================================================
FINAL VERIFICATION RESULTS
==================================================
✓ PASS | Database schema
✓ PASS | RLS policies
✓ PASS | Python imports
✓ PASS | Application init
✓ PASS | Aggregate creation
✓ PASS | Model serialization
==================================================
✓ ALL CHECKS PASSED - W1 + W2 COMPLETE
```

---

## Troubleshooting

### Problem: `psql: command not found`

**Solution:**
```bash
# macOS
brew install postgresql

# Ubuntu/Debian
sudo apt-get install postgresql-client

# Windows
# Download from https://www.postgresql.org/download/windows/
```

---

### Problem: `FATAL: database "corpus-kb" does not exist`

**Solution:**
```bash
psql -U postgres -c "CREATE DATABASE \"corpus-kb\";"
```

---

### Problem: `ImportError: No module named 'eventsourcing'`

**Solution:**
```bash
pip install -e .
```

---

### Problem: `ModuleNotFoundError: No module named 'src'`

**Solution:**
```bash
# Run from project root
cd /path/to/corpus-kb

# Or add to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

---

### Problem: `psycopg2.OperationalError: could not connect to server`

**Solution:**
```bash
# Check Postgres is running
psql -U postgres -c "SELECT 1;"

# If not running:
# macOS
brew services start postgresql

# Ubuntu/Debian
sudo systemctl start postgresql

# Windows
# Start PostgreSQL service from Services app
```

---

### Problem: `eventsourcing.exceptions.OperationalError: could not connect to database`

**Solution:**
1. Check config.yaml has correct connection string:
   ```yaml
   database:
     connection_string: "postgresql://postgres:postgres@localhost:5432/corpus-kb"
   ```
2. Replace `postgres:postgres` with your actual credentials
3. Ensure database exists: `psql -U postgres -c "CREATE DATABASE \"corpus-kb\";"`

---

## Success Criteria

You have successfully completed W1 + W2 when:

- [ ] All 7 tables exist in Postgres (events, documents, chunks, entities, relations, projection_checkpoints, tenants)
- [ ] All 5 RLS policies exist (documents_tenant_isolation, chunks_tenant_isolation, etc.)
- [ ] All Python imports resolve without error
- [ ] Application initializes successfully (get_app() returns CorpusApplication)
- [ ] Aggregates can be instantiated (Document, Entity, Relation)
- [ ] Models serialize to JSON (IngestFileCommand, SearchQuery, etc.)
- [ ] Final verification script passes all 6 checks

---

## Next Steps

Once W1 + W2 are complete:

1. **Commit your work:**
   ```bash
   git add src/domain/ src/migrations/ pyproject.toml config.yaml
   git commit -m "W1 + W2: Foundation + Domain Model"
   ```

2. **Proceed to W3 (Handlers):**
   - W3.1: CommandHandler
   - W3.2: QueryHandler

3. **Reference:** See `PLAN-DUAL-PROTOCOL-EVENTSOURCING.md` for W3+ details

---

## Time Summary

| Phase | Task | Time |
|-------|------|------|
| 1 | Database Setup | 5 min |
| 2 | Python Dependencies | 5 min |
| 3 | Create Domain Module | 15 min |
| 4 | Comprehensive Testing | 10 min |
| 5 | Final Verification | 5 min |
| **TOTAL** | | **40 min** |

**Actual time may vary:** 2.5-3 hours for first-time execution (includes reading, understanding, debugging). 30-40 minutes for experienced developers.


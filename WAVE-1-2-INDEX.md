# W1 & W2 Decomposition: Complete Index

**Project:** Corpus-KB Dual-Protocol Refactor  
**Scope:** WAVE 1 (Foundation) + WAVE 2 (Domain Model)  
**Status:** Ready for execution  
**Audience:** Junior developers, code decomposers, reviewers

---

## Document Map

### 1. **WAVE-1-2-DECOMPOSITION.md** (Primary Reference)
   - **Purpose:** Complete technical specification with exact code
   - **Content:**
     - W1.1: Postgres Schema (120 LOC SQL)
     - W1.2: Eventsourcing Application (60 LOC Python)
     - W1.3: RLS Policies (80 LOC SQL)
     - W2.1: Domain Aggregates (180 LOC Python)
     - W2.2: Pydantic Models (250 LOC Python)
   - **Use When:** You need the exact code to copy-paste
   - **Length:** ~1,500 lines

### 2. **WAVE-1-2-QUICK-REFERENCE.md** (Execution Checklist)
   - **Purpose:** Copy-paste ready edits and verification commands
   - **Content:**
     - File creation checklist
     - Dependency edits (pyproject.toml, config.yaml)
     - Directory structure
     - Execution order (sequential)
     - Common issues & fixes
     - Testing commands
     - Review checklist
   - **Use When:** You're executing the tasks and need quick lookups
   - **Length:** ~400 lines

### 3. **WAVE-1-2-EXECUTION-GUIDE.md** (Step-by-Step)
   - **Purpose:** Detailed walkthrough for first-time execution
   - **Content:**
     - Pre-execution checklist
     - Phase 1: Database Setup (5 min)
     - Phase 2: Python Dependencies (5 min)
     - Phase 3: Create Domain Module (15 min)
     - Phase 4: Comprehensive Testing (10 min)
     - Phase 5: Final Verification (5 min)
     - Troubleshooting guide
     - Success criteria
   - **Use When:** You're executing for the first time and want detailed guidance
   - **Length:** ~600 lines

### 4. **WAVE-1-2-INDEX.md** (This Document)
   - **Purpose:** Navigation and quick reference
   - **Content:** Document map, task summary, dependency graph, time estimates
   - **Use When:** You need to find something or understand the big picture
   - **Length:** ~300 lines

---

## Task Summary

### WAVE 1: Foundation (Postgres + Eventsourcing)

| Task | File | Type | LOC | Time | Status |
|------|------|------|-----|------|--------|
| W1.1 | `src/migrations/001_create_schema.sql` | SQL | 120 | 35 min | NEW |
| W1.2 | `src/domain/application.py` | Python | 60 | 30 min | NEW |
| W1.3 | `src/migrations/002_enable_rls.sql` | SQL | 80 | 40 min | NEW |
| **W1 Total** | | | **260** | **1.5 hrs** | |

### WAVE 2: Domain Model (DDD Aggregates)

| Task | File | Type | LOC | Time | Status |
|------|------|------|-----|------|--------|
| W2.1 | `src/domain/aggregates.py` | Python | 180 | 40 min | NEW |
| W2.2 | `src/domain/models.py` | Python | 250 | 35 min | NEW |
| **W2 Total** | | | **430** | **1.25 hrs** | |

### WAVE 1 + 2 Combined

| Metric | Value |
|--------|-------|
| Total LOC | 690 |
| Total Time (dev) | 2.5 hours |
| Total Time (review) | 1 hour |
| Total Time (both) | 3.5 hours |
| Files Created | 5 |
| Files Modified | 2 |
| Dependencies Added | 5 |

---

## Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│ WAVE 1: Foundation                                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  W1.1: Schema (SQL)                                         │
│    ├─ Creates: events, documents, chunks, entities,        │
│    │           relations, projection_checkpoints, tenants  │
│    └─ Depends on: Postgres 13+, pgvector, pgcrypto        │
│                                                             │
│  W1.2: Application (Python)                                │
│    ├─ Creates: CorpusApplication singleton                │
│    ├─ Depends on: W1.1 (schema must exist)                │
│    └─ Depends on: eventsourcing >= 10.0                   │
│                                                             │
│  W1.3: RLS (SQL)                                           │
│    ├─ Creates: RLS policies on all tables                 │
│    ├─ Depends on: W1.1 (tables must exist)                │
│    └─ Creates: corpus_admin, corpus_user roles            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ WAVE 2: Domain Model                                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  W2.1: Aggregates (Python)                                 │
│    ├─ Creates: Document, Entity, Relation aggregates      │
│    ├─ Depends on: W1.2 (application.py)                   │
│    └─ Depends on: eventsourcing >= 10.0                   │
│                                                             │
│  W2.2: Models (Python)                                     │
│    ├─ Creates: Command, Event, Query, Result models       │
│    ├─ Depends on: W2.1 (aggregates.py)                    │
│    └─ Depends on: pydantic >= 2.0                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Execution Order

**Parallel Execution (Recommended):**
- W1.1, W1.2, W1.3 can run in parallel (no code dependencies)
- W2.1, W2.2 can run in parallel (both depend on W1)

**Sequential Execution (Safer):**
1. W1.1 (Schema) — 35 min
2. W1.2 (Application) — 30 min
3. W1.3 (RLS) — 40 min
4. W2.1 (Aggregates) — 40 min
5. W2.2 (Models) — 35 min
**Total:** 180 min (3 hours)

---

## File Structure After W1 + W2

```
corpus-kb/
├── src/
│   ├── domain/                    # NEW
│   │   ├── __init__.py           # NEW (empty)
│   │   ├── application.py        # NEW (W1.2)
│   │   ├── aggregates.py         # NEW (W2.1)
│   │   └── models.py             # NEW (W2.2)
│   ├── migrations/               # NEW
│   │   ├── 001_create_schema.sql # NEW (W1.1)
│   │   └── 002_enable_rls.sql    # NEW (W1.3)
│   ├── chunking/                 # EXISTING
│   ├── storage/                  # EXISTING
│   ├── rag/                       # EXISTING
│   ├── tools/                     # EXISTING
│   ├── utils/                     # EXISTING
│   ├── config.py                 # EXISTING
│   └── validators.py             # EXISTING
├── pyproject.toml                # MOD (add 5 dependencies)
├── config.yaml                   # MOD (add database section)
└── [other files unchanged]
```

---

## Dependencies Added

### To pyproject.toml

```toml
"eventsourcing>=10.0",      # Event sourcing framework
"sqlalchemy>=2.0",          # SQL toolkit
"starlette>=0.35",          # ASGI web framework
"uvicorn>=0.24",            # ASGI server
"psycopg2-binary>=2.9",     # Postgres driver
```

### To config.yaml

```yaml
database:
  connection_string: "postgresql://postgres:postgres@localhost:5432/corpus-kb"
  echo: false
```

---

## Testing Strategy

### Unit Tests (Per Task)

| Task | Test | Command |
|------|------|---------|
| W1.1 | Schema exists | `psql corpus-kb -c "\dt" \| grep events` |
| W1.2 | App initializes | `python -c "from src.domain.application import get_app; get_app()"` |
| W1.3 | RLS enabled | `psql corpus-kb -c "\d documents" \| grep -i policies` |
| W2.1 | Aggregates import | `python -c "from src.domain.aggregates import Document"` |
| W2.2 | Models serialize | `python -c "from src.domain.models import IngestFileCommand; cmd = IngestFileCommand(...); cmd.model_dump_json()"` |

### Integration Tests (All Together)

See **WAVE-1-2-EXECUTION-GUIDE.md** → **PHASE 4: Comprehensive Testing**

### Final Verification

See **WAVE-1-2-EXECUTION-GUIDE.md** → **PHASE 5: Final Verification**

---

## Common Pitfalls & Solutions

| Pitfall | Solution |
|---------|----------|
| `psql: command not found` | Install Postgres client (brew/apt/Windows installer) |
| `FATAL: database "corpus-kb" does not exist` | `psql -U postgres -c "CREATE DATABASE \"corpus-kb\";"` |
| `ImportError: No module named 'eventsourcing'` | `pip install -e .` |
| `ModuleNotFoundError: No module named 'src'` | Run from project root or add to PYTHONPATH |
| `psycopg2.OperationalError: could not connect` | Ensure Postgres is running and database exists |
| `eventsourcing.exceptions.OperationalError` | Check connection string in config.yaml |

---

## Success Criteria

You have successfully completed W1 + W2 when:

- [ ] All 7 tables exist in Postgres
- [ ] All 5 RLS policies exist
- [ ] All Python imports resolve
- [ ] Application initializes successfully
- [ ] Aggregates instantiate correctly
- [ ] Models serialize to JSON
- [ ] Final verification script passes all 6 checks

---

## Next Steps (W3+)

After W1 + W2 are complete:

1. **Commit:** `git commit -m "W1 + W2: Foundation + Domain Model"`
2. **W3.1:** CommandHandler (uses W2.1 + W2.2)
3. **W3.2:** QueryHandler (uses W2.2)
4. **W4.1:** Postgres Projections (uses W3.1 + W3.2)
5. **W4.2:** LanceDB Projections (uses W3.1 + W3.2)
6. **W5.1:** HTTP Adapter (uses W4.1 + W4.2)
7. **W5.2:** Socket Adapter (uses W4.1 + W4.2)
8. **W5.3:** MCP Adapter Refactor (uses W4.1 + W4.2)

See **PLAN-DUAL-PROTOCOL-EVENTSOURCING.md** for W3+ details.

---

## Document Usage Guide

### For Execution

1. **Start here:** WAVE-1-2-EXECUTION-GUIDE.md
2. **Copy code from:** WAVE-1-2-DECOMPOSITION.md
3. **Quick lookup:** WAVE-1-2-QUICK-REFERENCE.md
4. **Verify:** WAVE-1-2-EXECUTION-GUIDE.md → PHASE 5

### For Review

1. **Architecture:** WAVE-1-2-DECOMPOSITION.md (intro + dependency graph)
2. **Code quality:** WAVE-1-2-DECOMPOSITION.md (each task section)
3. **Testing:** WAVE-1-2-QUICK-REFERENCE.md (review checklist)
4. **Integration:** WAVE-1-2-EXECUTION-GUIDE.md (PHASE 4 + 5)

### For Planning

1. **Timeline:** This document (time estimates)
2. **Dependencies:** This document (dependency graph)
3. **Effort:** This document (LOC + time per task)
4. **Risks:** WAVE-1-2-DECOMPOSITION.md (each task → RISK LEVEL)

---

## Time Estimates

### Development Time

| Task | Dev | Review | Total |
|------|-----|--------|-------|
| W1.1 | 20 min | 15 min | 35 min |
| W1.2 | 15 min | 15 min | 30 min |
| W1.3 | 20 min | 20 min | 40 min |
| W2.1 | 25 min | 15 min | 40 min |
| W2.2 | 20 min | 15 min | 35 min |
| **TOTAL** | **100 min** | **80 min** | **180 min** |

### Realistic Timeline

- **One developer (sequential):** 2.5-3 hours
- **Two developers (parallel W1 + W2):** 1.5-2 hours
- **First-time execution:** Add 30-60 min for reading, understanding, debugging

---

## Key Concepts

### Event Sourcing
Store all events (what happened) instead of current state. Replay events to reconstruct state.

### Aggregates
Root entities that enforce business rules. Each aggregate has a unique ID and can emit events.

### Projections
Read models built from events. Eventual consistency (events → projections → queries).

### Multi-Tenancy
Every table has `tenant_id`. RLS policies filter by `current_setting('app.tenant_id')`.

### Pydantic Models
Serializable data classes. Use for HTTP/socket transport.

---

## Contact & Support

- **Questions about W1 + W2?** See WAVE-1-2-EXECUTION-GUIDE.md → Troubleshooting
- **Need exact code?** See WAVE-1-2-DECOMPOSITION.md
- **Quick lookup?** See WAVE-1-2-QUICK-REFERENCE.md
- **Planning?** See this document (WAVE-1-2-INDEX.md)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-19 | Initial decomposition (W1 + W2) |

---

## Appendix: File Sizes

| File | Type | LOC | Size |
|------|------|-----|------|
| 001_create_schema.sql | SQL | 120 | ~4 KB |
| 002_enable_rls.sql | SQL | 80 | ~3 KB |
| application.py | Python | 60 | ~2 KB |
| aggregates.py | Python | 180 | ~6 KB |
| models.py | Python | 250 | ~8 KB |
| **TOTAL** | | **690** | **~23 KB** |

---

## Appendix: Import Dependencies

### New Imports (Python)

```python
# application.py
from eventsourcing.application import Application
from eventsourcing.domain import AggregateCreated

# aggregates.py
from eventsourcing.domain import Aggregate, event
from dataclasses import dataclass, field

# models.py
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum
```

### New Dependencies (pyproject.toml)

```toml
eventsourcing>=10.0
sqlalchemy>=2.0
starlette>=0.35
uvicorn>=0.24
psycopg2-binary>=2.9
```

### New Config (config.yaml)

```yaml
database:
  connection_string: "postgresql://postgres:postgres@localhost:5432/corpus-kb"
  echo: false
```

---

**Last Updated:** 2026-06-19  
**Status:** Ready for execution  
**Confidence:** High (all tasks specified, all code provided, all tests defined)


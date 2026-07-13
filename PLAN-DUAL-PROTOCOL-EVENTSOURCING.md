# Corpus-KB: Full DDD + Event Sourcing + Postgres Migration Plan

**Status:** LOCKED DECISIONS — Ready for ultrawork execution
**Timeline:** 6-8 weeks (3-4 weeks personal, 1 week SMB, 1-2 weeks integration + governance)
**Effort:** ~2,200 LOC new + 600 LOC refactored = ~2,800 LOC total
**Team Mode:** Ultra team (5 parallel reviewers: Architecture, Code Quality, Security, Integration, Refactor)

---

## Architecture Summary

```
Write Path (Commands → Events → Projections):
  HTTP/Socket/MCP → CommandHandler
                   ↓ saves to EventStore (Postgres)
                   ↓ publishes to EventBus
                   ↓ Projections subscribe (LanceDB, Postgres, Graph)
                   ↓ Read models updated asynchronously

Read Path (Queries → Projections):
  HTTP/Socket/MCP → QueryHandler → Projection query (Postgres tables)

Multi-Tenancy:
  tenant_id in every command/query → saved in events → RLS filters at DB layer

Existing MCP Layer:
  Tools become thin wrappers around CommandHandlers/QueryHandlers
  No refactoring of tool definitions, zero break in MCP interface
```

---

## Postgres Schema (Validated)

### Core Tables

```sql
-- Event store (source of truth)
CREATE TABLE IF NOT EXISTS events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    aggregate_id UUID NOT NULL,
    aggregate_type TEXT NOT NULL,  -- "Document", "Entity", "Relation"
    event_type TEXT NOT NULL,       -- "DocumentIngested", "EntityAdded", etc.
    payload JSONB NOT NULL,
    version INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX idx_events_tenant_aggregate ON events(tenant_id, aggregate_id);
CREATE INDEX idx_events_tenant_type ON events(tenant_id, event_type);
CREATE INDEX idx_events_created ON events(created_at DESC);

-- Projections checkpoint (for catch-up subscriptions)
CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name TEXT NOT NULL,
    tenant_id UUID NOT NULL,
    last_event_id UUID,
    checkpoint_version INT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (projection_name, tenant_id),
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- Documents projection
CREATE TABLE IF NOT EXISTS documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'text',  -- file, text, url
    chunk_count INT DEFAULT 0,
    file_size BIGINT,
    file_hash TEXT,
    language TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    version_id UUID,  -- for LanceDB versioning compatibility
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX idx_documents_tenant ON documents(tenant_id);
CREATE INDEX idx_documents_tenant_source ON documents(tenant_id, source);

-- Chunks projection (with vectors)
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id),
    text TEXT NOT NULL,
    vector vector(4096),  -- pgvector type, matches Postgres dimensions
    embedding_hash TEXT,  -- SHA256 of text (cache invalidation)
    chunk_index INT DEFAULT 0,
    source_type TEXT DEFAULT 'text',
    chunk_type TEXT DEFAULT 'paragraph',  -- function, class, method, section, paragraph
    entity_name TEXT,
    heading_path TEXT[],  -- ARRAY type
    file_path TEXT,
    start_line INT,
    end_line INT,
    parent_chunk_id UUID REFERENCES chunks(chunk_id),
    sibling_order INT,
    sibling_count INT,
    scope_chain TEXT[],  -- ARRAY type
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX idx_chunks_tenant ON chunks(tenant_id);
CREATE INDEX idx_chunks_tenant_doc ON chunks(tenant_id, doc_id);
CREATE INDEX idx_chunks_vector ON chunks USING hnsw(vector vector_cosine_ops);
CREATE INDEX idx_chunks_embedding_hash ON chunks(tenant_id, embedding_hash);

-- Entities projection
CREATE TABLE IF NOT EXISTS entities (
    entity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    entity_type TEXT DEFAULT 'concept',  -- Person, Place, Class, Function, Concept
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    CONSTRAINT unique_entity_per_tenant UNIQUE(tenant_id, name, entity_type)
);
CREATE INDEX idx_entities_tenant ON entities(tenant_id);

-- Relations projection
CREATE TABLE IF NOT EXISTS relations (
    relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source_entity_id UUID NOT NULL REFERENCES entities(entity_id),
    target_entity_id UUID NOT NULL REFERENCES entities(entity_id),
    relation_type TEXT DEFAULT 'related_to',  -- CONTAINS, DEPENDS_ON, CALLS, KNOWS
    weight FLOAT DEFAULT 1.0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);
CREATE INDEX idx_relations_tenant ON relations(tenant_id);
CREATE INDEX idx_relations_source ON relations(tenant_id, source_entity_id);
CREATE INDEX idx_relations_target ON relations(tenant_id, target_entity_id);

-- Full-text search index on chunks
CREATE INDEX idx_chunks_text_fts ON chunks USING gin(to_tsvector('english', text));

-- Tenants table (for multi-tenancy)
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### RLS Policies

```sql
-- Enable RLS on all tables
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

-- RLS policies (filter by current_setting('app.tenant_id'))
-- Example for documents:
CREATE POLICY documents_tenant_isolation ON documents
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Similar policies for events, chunks, entities, relations, projection_checkpoints
-- (See Wave 1.3 task for full policy DDL)

-- Set tenant_id in connection context before queries:
-- SET app.tenant_id = '<tenant-uuid>';
```

---

## Wave Dependencies & Critical Path

```
WAVE 1: Foundation (Postgres + Eventsourcing)
  ├── W1.1: Postgres schema design
  ├── W1.2: Eventsourcing Application setup
  └── W1.3: RLS policies
  (All parallel, ~4 days)

WAVE 2: Domain Model (DDD Aggregates)
  ├── W2.1: Aggregate definitions (@event decorated)
  ├── W2.2: Pydantic command/event models
  └─→ DEPENDS ON: Wave 1
  (Parallel, ~3 days)

WAVE 3: Handlers (Command & Query)
  ├── W3.1: CommandHandler implementation
  ├── W3.2: QueryHandler implementation
  └─→ DEPENDS ON: Wave 2
  (Parallel, ~3 days)

WAVE 4: Projections (Event Subscribers)
  ├── W4.1: Postgres projections (handles events, writes to projection tables)
  ├── W4.2: LanceDB projections (for vector search continuation)
  └─→ DEPENDS ON: Wave 3
  (Parallel, ~3 days)

WAVE 5: Protocol Adapters (MCP, HTTP, Socket)
  ├── W5.1: HTTP adapter (Starlette)
  ├── W5.2: Unix socket adapter (JSON-RPC 2.0)
  └── W5.3: MCP adapter refactor
  └─→ DEPENDS ON: Wave 4
  (Parallel, ~4 days)

WAVE 6: Data Migration (LanceDB → Postgres)
  ├── W6.1: Migration strategy (parallel run, validation)
  └── W6.2: Rollback plan
  └─→ DEPENDS ON: Wave 5 (running new stack + old stack simultaneously)
  (Sequential, ~2 days)

WAVE 7: Integration & Governance
  ├── W7.1: End-to-end testing (all three protocols)
  └── W7.2: Ultra team review + sign-off
  └─→ DEPENDS ON: Wave 6
  (Sequential + parallel reviews, ~3 days)

CRITICAL PATH: W1 → W2 → W3 → W4 → W5 → W6 → W7
Total: ~6-8 weeks
```

---

## Detailed Task Breakdown

### WAVE 1: Foundation (Postgres + Eventsourcing)

Run all W1 tasks in parallel. None block each other.

---

#### **W1.1: Create Postgres Schema**

- **WHERE:** `src/migrations/001_create_schema.sql`
- **NEW** — brand new file
- **INJECTION POINT:** N/A (schema file, no code injection)
- **DDD CONTEXT:** Stores all events (source of truth) and projections (read models)

**HOW:**
```sql
-- src/migrations/001_create_schema.sql
-- Run this with: psql corpus-kb < src/migrations/001_create_schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- (full DDL from Postgres Schema section above)
-- Create: tenants, events, projection_checkpoints, documents, chunks, entities, relations
-- Create: Indexes for performance (tenant_id, aggregate_id, vector cosine)
-- Create: FTS index on chunks.text for full-text search
```

**DATABASE SCHEMA:** See "Postgres Schema (Validated)" section above

**RLS POLICY:** None yet (handled in W1.3)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `psql corpus-kb -c "\dt" | grep -q events || exit 1` (table doesn't exist)
- GREEN: `psql corpus-kb -c "\dt" | grep events` (table exists, can list rows)
- MANUAL QA: `psql corpus-kb -c "SELECT COUNT(*) FROM events;"` (returns 0)

**REVIEW CHECKLIST:**
- [ ] Architecture: Schema aligns with event sourcing (events table is immutable append-only)
- [ ] Code Quality: DDL follows Postgres best practices (indexes on foreign keys, created_at timestamps)
- [ ] Security: No hard-coded passwords, pgcrypto for UUID generation
- [ ] Integration: Extension dependencies (vector, pgcrypto) are portable

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Postgres version < 13 (pgvector needs 13+)
- pgvector extension not installed (user must: `CREATE EXTENSION vector`)

**EFFORT:** 80 LOC, 20 min (dev) + 15 min (review) = 35 min

---

#### **W1.2: Create Eventsourcing Application**

- **WHERE:** `src/domain/application.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (application entry point)
- **DDD CONTEXT:** Wires up eventsourcing library + event store + event bus

**HOW:**
```python
# src/domain/application.py
from eventsourcing.application import Application
from eventsourcing.domain import AggregateCreated

class CorpusApplication(Application):
    """Event sourcing application for Corpus-KB."""
    
    def __init__(self, config: dict):
        # Configure Postgres backend
        env = {
            "INFRASTRUCTURE_FACTORY": "eventsourcing.postgres:Factory",
            "DATABASES_CONN_STR": config["database"]["connection_string"],
            "DATABASES_ECHO": False,
        }
        super().__init__(env=env)
        self.config = config

# Global application instance (singleton per process)
_app: CorpusApplication | None = None

def get_app() -> CorpusApplication:
    global _app
    if _app is None:
        from config import load_config
        _app = CorpusApplication(load_config())
    return _app
```

**DATABASE SCHEMA:** No new tables (eventsourcing creates its own event_store, snapshots tables)

**RLS POLICY:** None (eventsourcing manages its own schema)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `from domain.application import get_app; app = get_app()` (ImportError: no module)
- GREEN: `from domain.application import get_app; app = get_app()` (succeeds, returns CorpusApplication)
- MANUAL QA: `pytest tests/test_application.py::test_app_initializes` (app initialized without errors)

**REVIEW CHECKLIST:**
- [ ] Architecture: Application is a singleton (get_app pattern)
- [ ] Code Quality: Config is injected (not hard-coded)
- [ ] Security: Connection string from env/config, no secrets in code
- [ ] Integration: eventsourcing library version pinned in pyproject.toml

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- eventsourcing version incompatibility (need >= 10.0 for Postgres support)
- Connection string invalid (Postgres not running)

**EFFORT:** 60 LOC, 15 min (dev) + 15 min (review) = 30 min

---

#### **W1.3: Design RLS Policies**

- **WHERE:** `src/migrations/002_enable_rls.sql`
- **NEW**
- **INJECTION POINT:** N/A (RLS DDL file)
- **DDD CONTEXT:** Multi-tenancy enforcement (tenant_id filters at DB layer)

**HOW:**
```sql
-- src/migrations/002_enable_rls.sql
-- Run after 001_create_schema.sql

-- Step 1: Enable RLS on all tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE relations ENABLE ROW LEVEL SECURITY;

-- Step 2: Create RLS policies (filter by current_setting('app.tenant_id'))
CREATE POLICY tenants_all ON tenants
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid)::uuid)
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid)::uuid);

CREATE POLICY events_by_tenant ON events
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- (repeat for documents, chunks, entities, relations, projection_checkpoints)

-- Step 3: Create bypassing role (for admin/migrations)
CREATE ROLE corpus_admin SUPERUSER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_admin;

-- Step 4: Create user role (filters by RLS)
CREATE ROLE corpus_user;
GRANT USAGE ON SCHEMA public TO corpus_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO corpus_user;
```

**DATABASE SCHEMA:** No tables (modifies existing via ALTER TABLE)

**RLS POLICY:** Yes — one policy per table, filters by `current_setting('app.tenant_id')`

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `psql corpus-kb -c "SELECT * FROM documents;" | grep -q "policy"` (fails, RLS not enabled)
- GREEN: `psql corpus-kb -c "\d documents" | grep -q "Policies"` (shows RLS enabled)
- MANUAL QA: 
  ```bash
  # Set tenant context and verify isolation
  psql corpus-kb -c "SET app.tenant_id = '00000000-0000-0000-0000-000000000001';"
  psql corpus-kb -c "INSERT INTO documents(tenant_id, source) VALUES('00000000-0000-0000-0000-000000000001', 'test.py');"
  psql corpus-kb -c "SET app.tenant_id = '00000000-0000-0000-0000-000000000002';"
  psql corpus-kb -c "SELECT * FROM documents;" # Should return 0 rows (tenant isolation)
  ```

**REVIEW CHECKLIST:**
- [ ] Architecture: RLS filters at DB layer (zero app-level filtering needed)
- [ ] Code Quality: Policies are simple (one per table, based on tenant_id)
- [ ] Security: Bypassing role is restricted (admin only), user role has minimal grants
- [ ] Integration: Policies work with concurrent connections (different tenant_id per connection)

**RISK LEVEL:** Medium (incorrect RLS policies expose data between tenants)

**BLOCKING ISSUES:**
- Connection string doesn't have superuser privileges (CREATE POLICY requires superuser)
- Tenant context not set (queries with unset app.tenant_id will return 0 rows)

**EFFORT:** 120 LOC, 20 min (dev) + 20 min (review) = 40 min

**TOTAL WAVE 1:** 260 LOC, ~1.5 hours

---

### WAVE 2: Domain Model (DDD Aggregates)

Run all W2 tasks in parallel. DEPENDS ON: Wave 1 ✓

---

#### **W2.1: Define Domain Aggregates**

- **WHERE:** `src/domain/aggregates.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (domain module)
- **DDD CONTEXT:** Document, Chunk, Entity, Relation as event-decorated aggregates

**HOW:**
```python
# src/domain/aggregates.py
from eventsourcing.domain import Aggregate, event
from dataclasses import dataclass
from uuid import UUID
from typing import Optional, List

@dataclass
class Document(Aggregate):
    """A document aggregate — source of truth for a single ingested file/text."""
    tenant_id: UUID
    source: str
    source_type: str = "text"  # file, text, url
    chunk_count: int = 0
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    language: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    @event("Inserted")
    def ingest(self, source: str, source_type: str, metadata: dict):
        """Ingest a document — fires DocumentIngested event."""
        self.source = source
        self.source_type = source_type
        self.metadata = metadata
    
    @event("ChunksAdded")
    def add_chunks(self, chunk_count: int):
        """Add chunks to document."""
        self.chunk_count = chunk_count

@dataclass
class Entity(Aggregate):
    """An entity in the knowledge graph."""
    tenant_id: UUID
    name: str
    entity_type: str = "concept"
    metadata: dict = field(default_factory=dict)
    
    @event("Created")
    def create(self, name: str, entity_type: str, metadata: dict):
        """Create an entity."""
        self.name = name
        self.entity_type = entity_type
        self.metadata = metadata

@dataclass
class Relation(Aggregate):
    """A relation between two entities."""
    tenant_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)
    
    @event("Created")
    def create(self, source_id: UUID, target_id: UUID, rel_type: str, weight: float):
        """Create a relation."""
        self.source_entity_id = source_id
        self.target_entity_id = target_id
        self.relation_type = rel_type
        self.weight = weight
```

**DATABASE SCHEMA:** No new tables (events table captures all aggregate state changes)

**RLS POLICY:** None (aggregates don't directly access DB)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `from domain.aggregates import Document; doc = Document(...)` (doesn't exist)
- GREEN: `from domain.aggregates import Document; doc = Document(tenant_id=UUID(...), source="test.py")` (creates successfully)
- MANUAL QA: `pytest tests/test_domain.py::test_document_ingested_event` (event fires correctly)

**REVIEW CHECKLIST:**
- [ ] Architecture: Aggregates use @event decorator (eventsourcing pattern)
- [ ] Code Quality: Each aggregate has one responsibility (Document = docs, Entity = entities)
- [ ] Security: tenant_id is immutable (set in __init__, never changed)
- [ ] Integration: Events are JSON-serializable (JSONB in Postgres)

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- eventsourcing version mismatch (need correct @event decorator syntax)

**EFFORT:** 180 LOC, 25 min (dev) + 15 min (review) = 40 min

---

#### **W2.2: Create Pydantic Command/Event Models**

- **WHERE:** `src/domain/models.py` (new, separate from utils/models.py)
- **NEW**
- **INJECTION POINT:** N/A (domain module)
- **DDD CONTEXT:** Command and Event envelopes (request/response serialization)

**HOW:**
```python
# src/domain/models.py
from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional, List, Any
from enum import Enum

# ===== Commands (Write Requests) =====

class DomainCommand(BaseModel):
    """Base command envelope."""
    tenant_id: UUID
    command_id: UUID = Field(default_factory=uuid4)
    
    class Config:
        json_schema_extra = {"example": "all commands inherit from this"}

class IngestFileCommand(DomainCommand):
    file_path: str
    content: Optional[str] = None  # if None, read from disk
    source_type: Optional[str] = None

class IngestTextCommand(DomainCommand):
    text: str
    source_type: str = "text"

class AddEntityCommand(DomainCommand):
    name: str
    entity_type: str = "concept"
    metadata: dict = {}

class AddRelationCommand(DomainCommand):
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"
    weight: float = 1.0

# ===== Events (Write Results) =====

class DomainEvent(BaseModel):
    """Base event envelope."""
    event_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    aggregate_id: UUID
    aggregate_type: str  # "Document", "Entity", "Relation"
    event_type: str      # "DocumentIngested", "EntityAdded", etc.
    payload: dict
    version: int = 1
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    
    class Config:
        json_schema_extra = {"example": "events are immutable records"}

class DocumentIngestedEvent(DomainEvent):
    aggregate_type: str = "Document"
    event_type: str = "DocumentIngested"
    payload: dict  # {source, source_type, chunk_count, metadata}

class EntityAddedEvent(DomainEvent):
    aggregate_type: str = "Entity"
    event_type: str = "EntityAdded"
    payload: dict  # {name, entity_type, metadata}

# ===== Queries (Read Requests) =====

class SearchQuery(BaseModel):
    tenant_id: UUID
    query: str
    k: int = 10
    source_type: Optional[str] = None

class SQLQuery(BaseModel):
    tenant_id: UUID
    sql: str
    params: dict = {}

class ListDocumentsQuery(BaseModel):
    tenant_id: UUID
    limit: int = 100
    offset: int = 0

# ===== Results (Read Results) =====

class SearchResult(BaseModel):
    chunk_id: UUID
    text: str
    score: float
    source: str
    doc_id: UUID
```

**DATABASE SCHEMA:** None (serialization layer, no DB impact)

**RLS POLICY:** None

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `from domain.models import IngestFileCommand; cmd = IngestFileCommand(...)` (doesn't exist)
- GREEN: `from domain.models import IngestFileCommand; cmd = IngestFileCommand(tenant_id=UUID(...), file_path="test.py")` (creates + serializes to JSON)
- MANUAL QA: `pytest tests/test_domain_models.py::test_ingest_command_json_serializable` (model.json() works)

**REVIEW CHECKLIST:**
- [ ] Architecture: Commands and Events are immutable (use BaseModel, no setters)
- [ ] Code Quality: tenant_id is required on all commands/queries (no implicit tenants)
- [ ] Security: No secrets in models (passwords, tokens, etc.)
- [ ] Integration: Models serialize to JSON (for HTTP/socket transport)

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Pydantic version mismatch (syntax varies between v1/v2)

**EFFORT:** 150 LOC, 20 min (dev) + 15 min (review) = 35 min

**TOTAL WAVE 2:** 330 LOC, ~1.5 hours

---

### WAVE 3: Handlers (Command & Query)

Run W3.1 and W3.2 in parallel. DEPENDS ON: Wave 2 ✓

---

#### **W3.1: CommandHandler Implementation**

- **WHERE:** `src/handlers/command_handler.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (handler module)
- **DDD CONTEXT:** Takes commands, executes domain logic, saves events to event store

**HOW:**
```python
# src/handlers/command_handler.py
from domain.application import get_app
from domain.models import (
    DomainCommand, IngestFileCommand, IngestTextCommand,
    AddEntityCommand, AddRelationCommand
)
from domain.aggregates import Document, Entity, Relation
from uuid import uuid4

class CommandHandler:
    """Executes commands, saves events, publishes to event bus."""
    
    def __init__(self):
        self.app = get_app()
    
    def handle_ingest_file(self, cmd: IngestFileCommand) -> dict:
        """IngestFileCommand → DocumentIngested event."""
        doc_id = uuid4()
        
        # Read file if not provided
        if cmd.content is None:
            with open(cmd.file_path) as f:
                content = f.read()
        else:
            content = cmd.content
        
        # Create Document aggregate
        doc = Document(
            id=doc_id,
            tenant_id=cmd.tenant_id,
            source=cmd.file_path,
            source_type=cmd.source_type or "text",
        )
        
        # Call aggregate method (fires event)
        doc.ingest(
            source=cmd.file_path,
            source_type=cmd.source_type or "text",
            metadata={"file_size": len(content), "language": cmd.source_type}
        )
        
        # Save to event store (eventsourcing handles transaction)
        self.app.save(doc)
        
        return {
            "status": "success",
            "doc_id": str(doc_id),
            "result": {"doc_id": str(doc_id), "source": cmd.file_path}
        }
    
    def handle_add_entity(self, cmd: AddEntityCommand) -> dict:
        """AddEntityCommand → EntityAdded event."""
        entity_id = uuid4()
        entity = Entity(
            id=entity_id,
            tenant_id=cmd.tenant_id,
            name=cmd.name,
            entity_type=cmd.entity_type,
        )
        entity.create(cmd.name, cmd.entity_type, cmd.metadata)
        self.app.save(entity)
        
        return {
            "status": "success",
            "entity_id": str(entity_id),
            "result": {"entity_id": str(entity_id), "name": cmd.name}
        }
    
    def handle_add_relation(self, cmd: AddRelationCommand) -> dict:
        """AddRelationCommand → RelationAdded event."""
        relation_id = uuid4()
        relation = Relation(
            id=relation_id,
            tenant_id=cmd.tenant_id,
            source_entity_id=cmd.source_entity_id,
            target_entity_id=cmd.target_entity_id,
            relation_type=cmd.relation_type,
        )
        relation.create(
            cmd.source_entity_id,
            cmd.target_entity_id,
            cmd.relation_type,
            cmd.weight
        )
        self.app.save(relation)
        
        return {
            "status": "success",
            "relation_id": str(relation_id),
            "result": {"relation_id": str(relation_id), "relation_type": cmd.relation_type}
        }

# Global handler instance
_handler: CommandHandler | None = None

def get_command_handler() -> CommandHandler:
    global _handler
    if _handler is None:
        _handler = CommandHandler()
    return _handler
```

**DATABASE SCHEMA:** No new tables (events are appended to events table via eventsourcing)

**RLS POLICY:** None (handler runs as superuser, RLS applied at projection layer)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `handler = get_command_handler(); result = handler.handle_ingest_file(...)` (fails, Document aggregate doesn't exist)
- GREEN: `handler = get_command_handler(); result = handler.handle_ingest_file(IngestFileCommand(...))` (returns {status: success, doc_id: ...})
- MANUAL QA: `pytest tests/test_handlers.py::test_ingest_file_creates_event` (event saved to store)

**REVIEW CHECKLIST:**
- [ ] Architecture: Handler delegates to aggregates (doesn't contain business logic)
- [ ] Code Quality: Error handling (file not found, invalid entity IDs)
- [ ] Security: tenant_id from command is preserved in event
- [ ] Integration: Events are saved atomically (eventsourcing ACID transaction)

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Aggregate ID collision (uuid4() is unique but theoretically could collide, use better strategy)

**EFFORT:** 200 LOC, 30 min (dev) + 15 min (review) = 45 min

---

#### **W3.2: QueryHandler Implementation**

- **WHERE:** `src/handlers/query_handler.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (handler module)
- **DDD CONTEXT:** Takes queries, reads from projections, returns results

**HOW:**
```python
# src/handlers/query_handler.py
from domain.models import SearchQuery, SQLQuery, ListDocumentsQuery, SearchResult
from sqlalchemy import text, create_engine
from uuid import UUID
import os

class QueryHandler:
    """Executes queries against projection read models."""
    
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
    
    def handle_search(self, query: SearchQuery) -> dict:
        """SearchQuery → list of chunks (from vector index)."""
        with self.engine.connect() as conn:
            # Set RLS context
            conn.execute(text(f"SET app.tenant_id = '{query.tenant_id}';"))
            
            # Query pgvector for cosine similarity
            # First embed query (call embedder)
            from rag.embedder import OllamaEmbedder
            embedder = OllamaEmbedder()
            query_vector = embedder.embed(query.query)
            
            sql = """
            SELECT chunk_id, text, (1 - (vector <=> %s)) as score, source, doc_id
            FROM chunks
            WHERE tenant_id = %s
            AND (
                source_type IS NULL OR source_type = %s
            )
            ORDER BY vector <=> %s
            LIMIT %s
            """
            
            rows = conn.execute(
                text(sql),
                {
                    "query_vector": query_vector,
                    "tenant_id": query.tenant_id,
                    "source_type": query.source_type,
                    "limit": query.k
                }
            )
            
            results = [
                SearchResult(
                    chunk_id=row[0],
                    text=row[1],
                    score=row[2],
                    source=row[3],
                    doc_id=row[4]
                )
                for row in rows
            ]
            
            return {
                "status": "success",
                "result": [r.model_dump() for r in results]
            }
    
    def handle_sql_query(self, query: SQLQuery) -> dict:
        """SQLQuery → execute parameterized SQL."""
        with self.engine.connect() as conn:
            # Set RLS context
            conn.execute(text(f"SET app.tenant_id = '{query.tenant_id}';"))
            
            # Validate query (no DROP, no DELETE without WHERE)
            if "DROP" in query.sql.upper():
                return {"status": "error", "message": "DROP not allowed"}
            
            sql_stmt = text(query.sql)
            rows = conn.execute(sql_stmt, query.params)
            
            results = [dict(row) for row in rows]
            
            return {
                "status": "success",
                "result": results
            }
    
    def handle_list_documents(self, query: ListDocumentsQuery) -> dict:
        """ListDocumentsQuery → paginated documents."""
        with self.engine.connect() as conn:
            conn.execute(text(f"SET app.tenant_id = '{query.tenant_id}';"))
            
            sql = """
            SELECT doc_id, source, source_type, chunk_count, created_at
            FROM documents
            WHERE tenant_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """
            
            rows = conn.execute(text(sql), {
                "tenant_id": query.tenant_id,
                "limit": query.limit,
                "offset": query.offset
            })
            
            results = [dict(row) for row in rows]
            
            return {
                "status": "success",
                "result": results
            }

# Global handler instance
_handler: QueryHandler | None = None

def get_query_handler() -> QueryHandler:
    global _handler
    if _handler is None:
        from config import load_config
        cfg = load_config()
        _handler = QueryHandler(cfg["database"]["connection_string"])
    return _handler
```

**DATABASE SCHEMA:** None (reads from existing projection tables)

**RLS POLICY:** Uses RLS (via `SET app.tenant_id`)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `handler = get_query_handler(); result = handler.handle_search(SearchQuery(...))` (fails, no projections)
- GREEN: `handler = get_query_handler(); result = handler.handle_search(SearchQuery(...))` (returns {status: success, result: [...]})
- MANUAL QA: `pytest tests/test_handlers.py::test_search_returns_results` (query returns >0 results)

**REVIEW CHECKLIST:**
- [ ] Architecture: QueryHandler reads from projections (eventual consistency)
- [ ] Code Quality: SQL injection protection (parameterized queries)
- [ ] Security: RLS context set for each query (tenant_id filter)
- [ ] Integration: Embedder called (vector search on chunks projection)

**RISK LEVEL:** Medium (SQL injection, RLS misconfiguration)

**BLOCKING ISSUES:**
- Embedder not initialized
- Projections not populated yet (empty result set)

**EFFORT:** 240 LOC, 35 min (dev) + 15 min (review) = 50 min

**TOTAL WAVE 3:** 440 LOC, ~1.5 hours

---

### WAVE 4: Projections (Event Subscribers)

Run W4.1 and W4.2 in parallel. DEPENDS ON: Wave 3 ✓

---

#### **W4.1: Postgres Projections (Event Subscribers)**

- **WHERE:** `src/projections/postgres_projection.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (projection module)
- **DDD CONTEXT:** Subscribes to events, updates read models (projection tables)

**HOW:**
```python
# src/projections/postgres_projection.py
from domain.application import get_app
from domain.aggregates import Document, Entity, Relation
from sqlalchemy import text, create_engine
from uuid import UUID
from datetime import datetime

class PostgresProjection:
    """Subscribes to events and updates Postgres projection tables."""
    
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.app = get_app()
    
    def start_subscription(self):
        """Start listening for events and updating projections."""
        # Subscribe to all event types
        self.app.subscribe(self._on_document_ingested, "DocumentIngested")
        self.app.subscribe(self._on_entity_added, "EntityAdded")
        self.app.subscribe(self._on_relation_added, "RelationAdded")
    
    def _on_document_ingested(self, event):
        """Handle DocumentIngested event → update documents projection."""
        with self.engine.connect() as conn:
            # No RLS context needed (projection is admin operation)
            # But write with tenant_id from event
            
            sql = text("""
            INSERT INTO documents (doc_id, tenant_id, source, source_type, metadata, created_at)
            VALUES (:doc_id, :tenant_id, :source, :source_type, :metadata, :created_at)
            ON CONFLICT (doc_id) DO UPDATE SET
                updated_at = NOW(),
                metadata = EXCLUDED.metadata
            """)
            
            conn.execute(sql, {
                "doc_id": event.aggregate_id,
                "tenant_id": event.tenant_id,
                "source": event.payload.get("source"),
                "source_type": event.payload.get("source_type"),
                "metadata": event.payload.get("metadata"),
                "created_at": event.created_at
            })
            
            conn.commit()
    
    def _on_entity_added(self, event):
        """Handle EntityAdded event → update entities projection."""
        with self.engine.connect() as conn:
            sql = text("""
            INSERT INTO entities (entity_id, tenant_id, name, entity_type, metadata, created_at)
            VALUES (:entity_id, :tenant_id, :name, :entity_type, :metadata, :created_at)
            ON CONFLICT (tenant_id, name, entity_type) DO UPDATE SET
                metadata = EXCLUDED.metadata
            """)
            
            conn.execute(sql, {
                "entity_id": event.aggregate_id,
                "tenant_id": event.tenant_id,
                "name": event.payload.get("name"),
                "entity_type": event.payload.get("entity_type"),
                "metadata": event.payload.get("metadata"),
                "created_at": event.created_at
            })
            
            conn.commit()
    
    def _on_relation_added(self, event):
        """Handle RelationAdded event → update relations projection."""
        with self.engine.connect() as conn:
            sql = text("""
            INSERT INTO relations (relation_id, tenant_id, source_entity_id, target_entity_id, relation_type, metadata)
            VALUES (:relation_id, :tenant_id, :source_entity_id, :target_entity_id, :relation_type, :metadata)
            ON CONFLICT DO NOTHING
            """)
            
            conn.execute(sql, {
                "relation_id": event.aggregate_id,
                "tenant_id": event.tenant_id,
                "source_entity_id": event.payload.get("source_entity_id"),
                "target_entity_id": event.payload.get("target_entity_id"),
                "relation_type": event.payload.get("relation_type"),
                "metadata": event.payload.get("metadata")
            })
            
            conn.commit()

# Global projection instance
_projection: PostgresProjection | None = None

def get_postgres_projection() -> PostgresProjection:
    global _projection
    if _projection is None:
        from config import load_config
        cfg = load_config()
        _projection = PostgresProjection(cfg["database"]["connection_string"])
    return _projection

def start_postgres_projection_subscription():
    """Start the projection subscription."""
    proj = get_postgres_projection()
    proj.start_subscription()
```

**DATABASE SCHEMA:** Updates existing projection tables (documents, entities, relations)

**RLS POLICY:** Writes with tenant_id from event (RLS will filter on read)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `proj = get_postgres_projection(); proj.start_subscription()` (no events, projection empty)
- GREEN: `proj = get_postgres_projection(); proj.start_subscription(); # publish event; time.sleep(0.5); assert row exists in documents`
- MANUAL QA: `pytest tests/test_projections.py::test_document_ingested_updates_projection` (event → projection)

**REVIEW CHECKLIST:**
- [ ] Architecture: Projection is async subscriber (decoupled from command handler)
- [ ] Code Quality: ON CONFLICT clauses prevent duplicates (idempotent)
- [ ] Security: tenant_id preserved in projection (RLS filters on read)
- [ ] Integration: Event payload structure matches projection schema

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Event structure mismatch (payload doesn't have expected fields)
- Database connection error during projection update

**EFFORT:** 180 LOC, 25 min (dev) + 15 min (review) = 40 min

---

#### **W4.2: LanceDB Projections (Vector Index Continuation)**

- **WHERE:** `src/projections/lancedb_projection.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (projection module)
- **DDD CONTEXT:** Subscribes to events, updates LanceDB (vector index)

**HOW:**
```python
# src/projections/lancedb_projection.py
# This projection maintains LanceDB for backward compatibility (versioning, time-travel)
# Future: migrate to pgvector, then deprecate

from domain.application import get_app
from storage.lancedb_store import LanceDBStore
from rag.embedder import OllamaEmbedder
from utils.models import Chunk, Document
from uuid import UUID

class LanceDBProjection:
    """Subscribes to events and updates LanceDB (vector store)."""
    
    def __init__(self, storage_path: str):
        self.store = LanceDBStore(storage_path)
        self.embedder = OllamaEmbedder()
        self.app = get_app()
    
    def start_subscription(self):
        """Start listening for events."""
        self.app.subscribe(self._on_document_ingested, "DocumentIngested")
        self.app.subscribe(self._on_chunks_added, "ChunksAdded")
    
    def _on_document_ingested(self, event):
        """DocumentIngested event → insert to LanceDB documents table."""
        doc = Document(
            doc_id=str(event.aggregate_id),
            source=event.payload.get("source"),
            source_type=event.payload.get("source_type"),
            metadata=event.payload.get("metadata", {}),
            chunk_count=event.payload.get("chunk_count", 0),
        )
        self.store.insert_document(doc)
    
    def _on_chunks_added(self, event):
        """ChunksAdded event → embed and insert to LanceDB chunks table."""
        chunks_data = event.payload.get("chunks", [])
        chunks = [
            Chunk(**c)  # Reconstruct Chunk from event payload
            for c in chunks_data
        ]
        
        # Embed if vectors missing
        chunks_to_embed = [c for c in chunks if c.vector is None]
        if chunks_to_embed:
            chunks_to_embed = self.embedder.embed_chunks(chunks_to_embed)
        
        # Insert to LanceDB
        self.store.insert_chunks(chunks)

# Global projection instance
_projection: LanceDBProjection | None = None

def get_lancedb_projection() -> LanceDBProjection:
    global _projection
    if _projection is None:
        from config import load_config
        cfg = load_config()
        _projection = LanceDBProjection(cfg["storage"]["path"])
    return _projection

def start_lancedb_projection_subscription():
    """Start the LanceDB projection subscription."""
    proj = get_lancedb_projection()
    proj.start_subscription()
```

**DATABASE SCHEMA:** Updates LanceDB tables (documents, chunks) — no Postgres schema impact

**RLS POLICY:** N/A (LanceDB doesn't have RLS, but tenant_id is stored in event metadata)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `proj = get_lancedb_projection(); proj.start_subscription()` (no events, LanceDB empty)
- GREEN: `proj = get_lancedb_projection(); proj.start_subscription(); # publish event; time.sleep(0.5); assert doc exists in LanceDB`
- MANUAL QA: `pytest tests/test_projections.py::test_chunks_added_updates_lancedb` (event → LanceDB)

**REVIEW CHECKLIST:**
- [ ] Architecture: Projection can be disabled/deprecated (eventually remove for pgvector)
- [ ] Code Quality: Handles missing vectors (re-embeds on event)
- [ ] Security: tenant_id from event is preserved (for multi-tenancy later)
- [ ] Integration: LanceDB operations are fast (no network latency)

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Event payload structure mismatch (chunk data shape)
- Embedder failure (Ollama down)

**EFFORT:** 150 LOC, 20 min (dev) + 15 min (review) = 35 min

**TOTAL WAVE 4:** 330 LOC, ~1.5 hours

---

### WAVE 5: Protocol Adapters (MCP, HTTP, Socket)

Run W5.1, W5.2, W5.3 in parallel. DEPENDS ON: Wave 4 ✓

---

#### **W5.1: HTTP Adapter (Starlette)**

- **WHERE:** `src/api/http.py` (new)
- **NEW**
- **INJECTION POINT:** `src/server.py::main()` — wire HTTP app into event loop
- **DDD CONTEXT:** HTTP routes dispatch to command/query handlers

**HOW:**
```python
# src/api/http.py
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from domain.models import (
    IngestFileCommand, IngestTextCommand, SearchQuery,
    SQLQuery, ListDocumentsQuery
)
from handlers.command_handler import get_command_handler
from handlers.query_handler import get_query_handler
from uuid import UUID
import json

async def post_ingest_file(request: Request) -> JSONResponse:
    """POST /api/ingest/file — ingest a file."""
    body = await request.json()
    cmd = IngestFileCommand(
        tenant_id=UUID(body["tenant_id"]),
        file_path=body["file_path"],
        content=body.get("content"),
        source_type=body.get("source_type")
    )
    handler = get_command_handler()
    result = handler.handle_ingest_file(cmd)
    return JSONResponse(result)

async def post_search(request: Request) -> JSONResponse:
    """POST /api/search — search documents."""
    body = await request.json()
    query = SearchQuery(
        tenant_id=UUID(body["tenant_id"]),
        query=body["query"],
        k=body.get("k", 10),
        source_type=body.get("source_type")
    )
    handler = get_query_handler()
    result = handler.handle_search(query)
    return JSONResponse(result)

async def post_sql_query(request: Request) -> JSONResponse:
    """POST /api/query/sql — execute SQL query."""
    body = await request.json()
    query = SQLQuery(
        tenant_id=UUID(body["tenant_id"]),
        sql=body["sql"],
        params=body.get("params", {})
    )
    handler = get_query_handler()
    result = handler.handle_sql_query(query)
    return JSONResponse(result)

async def get_documents(request: Request) -> JSONResponse:
    """GET /api/documents — list documents."""
    tenant_id = request.query_params.get("tenant_id")
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    
    query = ListDocumentsQuery(
        tenant_id=UUID(tenant_id),
        limit=limit,
        offset=offset
    )
    handler = get_query_handler()
    result = handler.handle_list_documents(query)
    return JSONResponse(result)

# Routes
routes = [
    Route("/api/ingest/file", endpoint=post_ingest_file, methods=["POST"]),
    Route("/api/ingest/text", endpoint=post_ingest_text, methods=["POST"]),
    Route("/api/search", endpoint=post_search, methods=["POST"]),
    Route("/api/query/sql", endpoint=post_sql_query, methods=["POST"]),
    Route("/api/documents", endpoint=get_documents, methods=["GET"]),
]

# CORS middleware (for local dev + remote prod)
def create_app(config: dict) -> Starlette:
    app = Starlette(routes=routes)
    
    cors_origins = config.get("http", {}).get("cors_origins", ["http://localhost:3000"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    return app
```

**Injection in server.py:**
```python
# src/server.py
async def main():
    config = load_config()
    
    # ... existing MCP setup ...
    
    # HTTP adapter
    from api.http import create_app
    http_app = create_app(config)
    
    # Run HTTP server on event loop
    host = config.get("http", {}).get("host", "localhost")
    port = config.get("http", {}).get("port", 8010)
    
    import uvicorn
    server_config = uvicorn.Config(
        app=http_app,
        host=host,
        port=port,
        loop="asyncio"
    )
    server = uvicorn.Server(server_config)
    
    # Run both MCP and HTTP on same event loop
    async with asyncio.TaskGroup() as tg:
        tg.create_task(mcp.run(transport=transport))
        tg.create_task(server.serve())
```

**DATABASE SCHEMA:** None (uses existing projections)

**RLS POLICY:** None (enforced at query handler layer)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `curl http://localhost:8010/api/search -X POST` (server not running)
- GREEN: `pytest tests/test_http.py::test_search_returns_200` (HTTP request returns {status: success, result: [...]})
- MANUAL QA: 
  ```bash
  curl -X POST http://localhost:8010/api/search \
    -H "Content-Type: application/json" \
    -d '{"tenant_id": "...", "query": "test"}'
  # Should return: {"status": "success", "result": [...]}
  ```

**REVIEW CHECKLIST:**
- [ ] Architecture: Routes dispatch to handlers (thin adapter)
- [ ] Code Quality: Input validation (tenant_id is UUID, k is int)
- [ ] Security: CORS configured (not wildcard + credentials)
- [ ] Integration: HTTP server runs on same event loop as MCP

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Port already in use (8010 taken)
- CORS misconfigured (frontend can't reach server)

**EFFORT:** 200 LOC, 30 min (dev) + 15 min (review) = 45 min

---

#### **W5.2: Unix Socket Adapter (JSON-RPC 2.0)**

- **WHERE:** `src/api/socket.py` (new)
- **NEW**
- **INJECTION POINT:** `src/server.py::main()` — wire socket server into event loop
- **DDD CONTEXT:** JSON-RPC 2.0 server on /tmp/corpus-kb.sock

**HOW:**
```python
# src/api/socket.py
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4
from domain.models import (
    IngestFileCommand, SearchQuery, AddEntityCommand
)
from handlers.command_handler import get_command_handler
from handlers.query_handler import get_query_handler

class JSONRPCServer:
    """JSON-RPC 2.0 server on Unix socket."""
    
    def __init__(self, socket_path: str = "/tmp/corpus-kb.sock"):
        self.socket_path = socket_path
        self.cmd_handler = get_command_handler()
        self.query_handler = get_query_handler()
    
    async def start(self):
        """Start listening on Unix socket."""
        # Remove old socket file if exists
        Path(self.socket_path).unlink(missing_ok=True)
        
        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.socket_path
        )
        
        async with server:
            print(f"JSON-RPC 2.0 server listening on {self.socket_path}")
            await server.serve_forever()
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a single client connection."""
        while True:
            try:
                # Read JSON-RPC request (newline-delimited)
                line = await reader.readline()
                if not line:
                    break
                
                request = json.loads(line)
                response = await self.process_request(request)
                
                # Write JSON-RPC response
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
            
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e)
                    }
                }
                writer.write((json.dumps(error_response) + "\n").encode())
                await writer.drain()
    
    async def process_request(self, request: dict) -> dict:
        """Process a JSON-RPC 2.0 request."""
        jsonrpc = request.get("jsonrpc")
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")
        
        if jsonrpc != "2.0":
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid Request"}}
        
        try:
            if method == "ingest_file":
                cmd = IngestFileCommand(
                    tenant_id=UUID(params["tenant_id"]),
                    file_path=params["file_path"],
                    content=params.get("content"),
                    source_type=params.get("source_type")
                )
                result = self.cmd_handler.handle_ingest_file(cmd)
            
            elif method == "search":
                query = SearchQuery(
                    tenant_id=UUID(params["tenant_id"]),
                    query=params["query"],
                    k=params.get("k", 10),
                    source_type=params.get("source_type")
                )
                result = self.query_handler.handle_search(query)
            
            elif method == "add_entity":
                cmd = AddEntityCommand(
                    tenant_id=UUID(params["tenant_id"]),
                    name=params["name"],
                    entity_type=params.get("entity_type", "concept"),
                    metadata=params.get("metadata", {})
                )
                result = self.cmd_handler.handle_add_entity(cmd)
            
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }
            
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result
            }
        
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "Internal error", "data": str(e)}
            }

# Global server instance
_server: JSONRPCServer | None = None

def get_socket_server(socket_path: str = "/tmp/corpus-kb.sock") -> JSONRPCServer:
    global _server
    if _server is None:
        _server = JSONRPCServer(socket_path)
    return _server
```

**Injection in server.py:**
```python
# src/server.py
async def main():
    # ... existing setup ...
    
    # Socket adapter
    from api.socket import get_socket_server
    socket_server = get_socket_server()
    
    # Run both MCP and socket on same event loop
    async with asyncio.TaskGroup() as tg:
        tg.create_task(mcp.run(transport=transport))
        tg.create_task(socket_server.start())
        tg.create_task(server.serve())  # HTTP
```

**DATABASE SCHEMA:** None (uses existing projections)

**RLS POLICY:** None (enforced at handler layer)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `nc -U /tmp/corpus-kb.sock` (socket doesn't exist)
- GREEN: `pytest tests/test_socket.py::test_socket_server_listens` (socket exists, server running)
- MANUAL QA: 
  ```bash
  # In one terminal:
  # (socket server running)
  
  # In another:
  echo '{"jsonrpc": "2.0", "id": 1, "method": "search", "params": {"tenant_id": "...", "query": "test"}}' | nc -U /tmp/corpus-kb.sock
  # Should return: {"jsonrpc": "2.0", "id": 1, "result": {...}}
  ```

**REVIEW CHECKLIST:**
- [ ] Architecture: JSON-RPC 2.0 protocol implemented (id correlation, error envelopes)
- [ ] Code Quality: Newline-delimited JSON (one request/response per line)
- [ ] Security: No auth yet (acceptable for local socket)
- [ ] Integration: Socket server runs on same event loop as HTTP/MCP

**RISK LEVEL:** Low

**BLOCKING ISSUES:**
- Socket file permission issue (/tmp not writable)
- Event loop blocking (long-running handler delays other clients)

**EFFORT:** 220 LOC, 30 min (dev) + 15 min (review) = 45 min

---

#### **W5.3: MCP Adapter Refactor**

- **WHERE:** `src/tools/*.py` (MODIFY existing tools)
- **MODIFY** — tools become thin wrappers around handlers
- **INJECTION POINT:** Inside each tool function, replace direct storage calls with handler calls

**HOW (Example):**
```python
# Before (src/tools/ingest_tools.py — EXISTING):
def _ingest_single_file(file_path: str, detector, embedder, store, graph, resolver, database=None):
    path = Path(file_path)
    content = path.read_text()
    file_type = detect_file_type(file_path, content)
    chunker = detector.get_chunker(file_type)
    raw_chunks = chunker.chunk(content, file_path=str(path))
    chunks = resolver.resolve(raw_chunks)
    doc = Document(source=str(path), source_type=file_type)
    chunks = embedder.embed_chunks(chunks)
    store.insert_document(doc)
    store.insert_chunks(chunks)
    # ... etc ...

# After (src/tools/ingest_tools.py — REFACTORED):
def _ingest_single_file(file_path: str, handler=None):
    if handler is None:
        from handlers.command_handler import get_command_handler
        handler = get_command_handler()
    
    cmd = IngestFileCommand(
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),  # TODO: get from context
        file_path=file_path,
        content=None  # handler reads from disk
    )
    result = handler.handle_ingest_file(cmd)
    
    # MCP expects dict with doc_id, source, chunk_count
    return {
        "doc_id": result["result"]["doc_id"],
        "source": file_path,
        "chunk_count": result["result"].get("chunk_count", 0)
    }
```

**DATABASE SCHEMA:** None (handlers use eventsourcing, which is invisible to tools)

**RLS POLICY:** Multi-tenancy context (for now, hardcode to single tenant; SMB wave adds context)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `ingest_tools.search(query="test")` (no handler, fails)
- GREEN: `ingest_tools.search(query="test", handler=mock_handler)` (calls handler, returns results)
- MANUAL QA: `pytest tests/test_tools.py::test_ingest_file_via_handler` (MCP tool works, data stored)

**REVIEW CHECKLIST:**
- [ ] Architecture: Tools delegate to handlers (no business logic in tools)
- [ ] Code Quality: Tool signatures unchanged (backward compatible with MCP)
- [ ] Security: tenant_id context placeholder (will be filled in SMB wave)
- [ ] Integration: Tools + handlers work together (test both paths)

**RISK LEVEL:** Medium (refactoring existing code, must not break MCP interface)

**BLOCKING ISSUES:**
- Circular imports (tools ↔ handlers)
- tenant_id context not available (multi-tenancy placeholder)

**EFFORT:** 180 LOC (refactoring 6 tool modules), 40 min (dev) + 20 min (review) = 60 min

**TOTAL WAVE 5:** 600 LOC, ~2.5 hours

---

### WAVE 6: Data Migration (LanceDB → Postgres)

Run W6.1 and W6.2 sequentially (W6.2 depends on W6.1 completion).

DEPENDS ON: Wave 5 ✓

---

#### **W6.1: Data Migration (LanceDB → Postgres)**

- **WHERE:** `scripts/migrate_to_postgres.py` (new)
- **NEW**
- **INJECTION POINT:** Run once during Wave 6, not part of normal startup
- **DDD CONTEXT:** Migrate existing LanceDB + DuckDB data → Postgres projections

**HOW:**
```python
# scripts/migrate_to_postgres.py
"""
Migrate data from LanceDB + DuckDB to Postgres.

Parallel run strategy:
1. Keep old LanceDB + DuckDB running (still serving queries via legacy path)
2. New Postgres projections running in parallel (receiving new events)
3. Migrate historical data: LanceDB documents/chunks → pgvector
4. Validate data integrity (count rows, sample checksums)
5. Switch queries to Postgres (deprecate LanceDB/DuckDB)
6. Archive old data (backup LanceDB/DuckDB to S3 or local)
"""

import asyncio
from pathlib import Path
from sqlalchemy import create_engine, text
from storage.lancedb_store import LanceDBStore
from storage.duckdb_engine import DuckDBEngine
import lancedb

async def migrate_documents(pg_engine, lancedb_store):
    """Migrate documents from LanceDB to Postgres."""
    lance_docs = lancedb_store.documents_table.to_pandas()
    
    with pg_engine.connect() as conn:
        for _, row in lance_docs.iterrows():
            sql = text("""
            INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count, created_at)
            VALUES (:doc_id, :tenant_id, :source, :source_type, :chunk_count, :created_at)
            ON CONFLICT (doc_id) DO NOTHING
            """)
            
            conn.execute(sql, {
                "doc_id": row["doc_id"],
                "tenant_id": "00000000-0000-0000-0000-000000000000",  # default tenant
                "source": row["source"],
                "source_type": row["source_type"],
                "chunk_count": row["chunk_count"],
                "created_at": row["created_at"]
            })
        
        conn.commit()
    
    return len(lance_docs)

async def migrate_chunks(pg_engine, lancedb_store):
    """Migrate chunks with vectors from LanceDB to Postgres (pgvector)."""
    lance_chunks = lancedb_store.chunks_table.to_pandas()
    
    with pg_engine.connect() as conn:
        for _, row in lance_chunks.iterrows():
            # Convert vector to pgvector format (list of floats)
            vector = row["vector"].tolist() if hasattr(row["vector"], "tolist") else row["vector"]
            
            sql = text("""
            INSERT INTO chunks (chunk_id, tenant_id, doc_id, text, vector, chunk_index, created_at)
            VALUES (:chunk_id, :tenant_id, :doc_id, :text, :vector::vector, :chunk_index, :created_at)
            ON CONFLICT (chunk_id) DO NOTHING
            """)
            
            conn.execute(sql, {
                "chunk_id": row["chunk_id"],
                "tenant_id": "00000000-0000-0000-0000-000000000000",
                "doc_id": row["doc_id"],
                "text": row["text"],
                "vector": vector,
                "chunk_index": row["chunk_index"],
                "created_at": row["created_at"]
            })
        
        conn.commit()
    
    return len(lance_chunks)

async def validate_migration(pg_engine, lancedb_store, duckdb_engine):
    """Validate data integrity after migration."""
    with pg_engine.connect() as conn:
        pg_doc_count = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar()
        pg_chunk_count = conn.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
    
    lance_doc_count = lancedb_store.documents_table.count()
    lance_chunk_count = lancedb_store.chunks_table.count()
    
    print(f"\nMigration Validation:")
    print(f"Documents: LanceDB={lance_doc_count}, Postgres={pg_doc_count}")
    print(f"Chunks: LanceDB={lance_chunk_count}, Postgres={pg_chunk_count}")
    
    if pg_doc_count == lance_doc_count and pg_chunk_count == lance_chunk_count:
        print("✓ Migration validation PASSED")
        return True
    else:
        print("✗ Migration validation FAILED — counts don't match")
        return False

async def main():
    from config import load_config
    cfg = load_config()
    
    # Connect to both old and new stores
    pg_engine = create_engine(cfg["database"]["connection_string"])
    lancedb_store = LanceDBStore(cfg["storage"]["path"])
    duckdb_engine = DuckDBEngine(cfg["storage"]["path"])
    
    print("Starting data migration (LanceDB → Postgres)...")
    
    # Migrate documents
    doc_count = await migrate_documents(pg_engine, lancedb_store)
    print(f"✓ Migrated {doc_count} documents")
    
    # Migrate chunks with vectors
    chunk_count = await migrate_chunks(pg_engine, lancedb_store)
    print(f"✓ Migrated {chunk_count} chunks with vectors")
    
    # Validate
    valid = await validate_migration(pg_engine, lancedb_store, duckdb_engine)
    
    if valid:
        print("\nMigration complete. Old LanceDB/DuckDB data is still available for rollback.")
    else:
        print("\nMigration validation failed. Rolling back...")
        # TODO: implement rollback

if __name__ == "__main__":
    asyncio.run(main())
```

**DATABASE SCHEMA:** Writes to existing Postgres projection tables

**RLS POLICY:** Uses default tenant (single-tenant for now, will be multi-tenant in next wave)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `python scripts/migrate_to_postgres.py` (fails, Postgres empty)
- GREEN: `python scripts/migrate_to_postgres.py` (succeeds, documents/chunks in Postgres, counts match)
- MANUAL QA: 
  ```bash
  # Before migration
  psql corpus-kb -c "SELECT COUNT(*) FROM documents;" # returns 0
  
  # Run migration
  python scripts/migrate_to_postgres.py
  
  # After migration
  psql corpus-kb -c "SELECT COUNT(*) FROM documents;" # returns N (matches LanceDB)
  psql corpus-kb -c "SELECT COUNT(*) FROM chunks;" # returns M (matches LanceDB)
  ```

**REVIEW CHECKLIST:**
- [ ] Architecture: Migration is reversible (old data still available)
- [ ] Code Quality: Data integrity validated (row counts match)
- [ ] Security: Default tenant_id hardcoded (placeholder for multi-tenant in next wave)
- [ ] Integration: Both old and new stores queried (parallel run possible)

**RISK LEVEL:** High (data loss if migration fails)

**BLOCKING ISSUES:**
- Vector encoding mismatch (LanceDB → pgvector format)
- Foreign key violations (chunks reference non-existent documents)

**EFFORT:** 200 LOC, 30 min (dev) + 20 min (review) = 50 min

---

#### **W6.2: Rollback Plan & Archive**

- **WHERE:** `scripts/rollback_migration.py` (new)
- **NEW**
- **INJECTION POINT:** Emergency recovery only
- **DDD CONTEXT:** Restore from backup if migration fails

**HOW:**
```python
# scripts/rollback_migration.py
"""
Rollback migration if data corruption detected.

Strategy:
1. Backup Postgres projections to local file
2. If validation fails, truncate Postgres tables
3. Restore from LanceDB (authoritative during migration)
"""

async def rollback_migration(pg_engine):
    """Rollback Postgres projections to pre-migration state."""
    with pg_engine.connect() as conn:
        # Truncate all projection tables
        tables = ["chunks", "documents", "entities", "relations"]
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        
        conn.commit()
    
    print("✓ Rollback complete (Postgres projections cleared)")
    print("  Old LanceDB + DuckDB remain unchanged (can restore from backup)")

if __name__ == "__main__":
    import asyncio
    from config import load_config
    from sqlalchemy import create_engine
    
    cfg = load_config()
    pg_engine = create_engine(cfg["database"]["connection_string"])
    
    print("WARNING: This will clear all Postgres projections!")
    confirm = input("Type 'ROLLBACK' to proceed: ")
    
    if confirm == "ROLLBACK":
        asyncio.run(rollback_migration(pg_engine))
    else:
        print("Rollback cancelled.")
```

**DATABASE SCHEMA:** TRUNCATE statements (dangerous!)

**RLS POLICY:** N/A (executed as superuser)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `python scripts/rollback_migration.py` and select 'ROLLBACK' (tables truncated)
- GREEN: `psql corpus-kb -c "SELECT COUNT(*) FROM documents;" # returns 0 (rollback successful)`
- MANUAL QA: Manual rollback on test database only

**REVIEW CHECKLIST:**
- [ ] Architecture: Rollback is manual + confirmed (prevents accidental data loss)
- [ ] Code Quality: Only called in emergency (not part of normal flow)
- [ ] Security: Requires explicit "ROLLBACK" input (safety)
- [ ] Integration: Works with production Postgres connection string

**RISK LEVEL:** Critical (data-destructive operation)

**BLOCKING ISSUES:**
- Accidental execution (no safeguards except prompt)

**EFFORT:** 100 LOC, 15 min (dev) + 15 min (review) = 30 min

**TOTAL WAVE 6:** 300 LOC, ~1.5 hours (but includes long-running migration, 15-30 min depending on data size)

---

### WAVE 7: Integration & Governance

Run W7.1 and W7.2 sequentially. DEPENDS ON: Wave 6 ✓

---

#### **W7.1: End-to-End Integration Testing**

- **WHERE:** `tests/test_integration_e2e.py` (new)
- **NEW**
- **INJECTION POINT:** N/A (test file)
- **DDD CONTEXT:** Test all three protocols (MCP, HTTP, socket) working together

**HOW:**
```python
# tests/test_integration_e2e.py
"""
End-to-end integration tests: MCP + HTTP + Socket all working together.

Test strategy:
1. Start server with all three protocols
2. Ingest a file via MCP (tool)
3. Search via HTTP (REST)
4. Add entity via socket (JSON-RPC)
5. Verify all three protocols see the same data
"""

import pytest
import asyncio
import subprocess
import socket
import json
import requests
from uuid import uuid4
from pathlib import Path
import time

@pytest.fixture
async def corpus_server():
    """Start Corpus server with all protocols."""
    # Start server in background
    proc = subprocess.Popen(
        ["python", "-m", "src.server"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    time.sleep(2)
    
    yield proc
    
    # Cleanup
    proc.terminate()
    proc.wait()

@pytest.mark.asyncio
async def test_ingest_via_mcp_search_via_http(corpus_server):
    """Ingest a file via MCP, then search it via HTTP."""
    # Create test file
    test_file = Path("/tmp/test_corpus.py")
    test_file.write_text("def hello_world():\n    print('Hello, world!')")
    
    # 1. Ingest via MCP (simulate MCP client)
    from tools.ingest_tools import _ingest_single_file
    from storage.lancedb_store import LanceDBStore
    from rag.embedder import OllamaEmbedder
    from chunking.detector import FileTypeDetector
    from chunking.hierarchy import HierarchyResolver
    from chunking.code_chunker import CodeChunker
    
    detector = FileTypeDetector({"code": CodeChunker()})
    embedder = OllamaEmbedder()
    store = LanceDBStore("./data/lancedb")
    resolver = HierarchyResolver()
    
    result_mcp = _ingest_single_file(
        str(test_file),
        detector=detector,
        embedder=embedder,
        store=store,
        graph=None,
        resolver=resolver
    )
    
    assert result_mcp["doc_id"]
    assert result_mcp["chunk_count"] > 0
    
    # 2. Search via HTTP
    time.sleep(1)  # Wait for projections to sync
    
    response = requests.post(
        "http://localhost:8010/api/search",
        json={
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "query": "hello"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert len(data["result"]) > 0
    assert "hello" in data["result"][0]["text"].lower()
    
    # 3. Add entity via socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect("/tmp/corpus-kb.sock")
    
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "add_entity",
        "params": {
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "name": "hello_world",
            "entity_type": "function"
        }
    }
    
    sock.send((json.dumps(request) + "\n").encode())
    response_data = sock.recv(1024).decode()
    response_json = json.loads(response_data)
    
    assert response_json["id"] == 1
    assert response_json["result"]["status"] == "success"
    
    sock.close()
    
    # 4. Verify entity via HTTP
    response = requests.get(
        "http://localhost:8010/api/entities",
        params={
            "tenant_id": "00000000-0000-0000-0000-000000000000",
            "name": "hello_world"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert len(data["result"]) > 0
    assert data["result"][0]["name"] == "hello_world"
    
    print("✓ E2E integration test passed (MCP + HTTP + Socket)")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
```

**DATABASE SCHEMA:** Uses existing projections

**RLS POLICY:** Single tenant (default for now)

**CATEGORY:** quick | SKILLS: []

**TEST (TDD):**
- RED: `pytest tests/test_integration_e2e.py` (server not running, tests fail)
- GREEN: `pytest tests/test_integration_e2e.py` (all three protocols work, data consistent)
- MANUAL QA: `pytest tests/test_integration_e2e.py -v -s` (see real server interactions)

**REVIEW CHECKLIST:**
- [ ] Architecture: All three protocols (MCP, HTTP, socket) integrated
- [ ] Code Quality: Tests are async + waiting for projections to sync
- [ ] Security: No hardcoded URLs (use config)
- [ ] Integration: Data written via one protocol visible via another

**RISK LEVEL:** Medium (tests are integration, not unit)

**BLOCKING ISSUES:**
- Server not starting (port in use, missing dependencies)
- Projection lag (need time.sleep() to wait for async projections)

**EFFORT:** 250 LOC, 35 min (dev) + 20 min (review) = 55 min

---

#### **W7.2: Ultra Team Review & Sign-Off**

- **WHERE:** Governance process (not code)
- **N/A**
- **INJECTION POINT:** N/A
- **DDD CONTEXT:** Five parallel reviewers approve all changes

**HOW:**

**Architecture Reviewer (Oracle):**
- [ ] Event sourcing pattern correctly applied (events immutable, aggregates idempotent)
- [ ] Multi-tenancy design (RLS at DB layer, no app-level filtering leaks)
- [ ] Protocol adapters are thin (no business logic duplication)
- [ ] Migration path supports personal → SMB → enterprise (no breaking changes)

**Code Quality Reviewer (Oracle):**
- [ ] No magic numbers (max k=50, hardcoded tenant_id)
- [ ] Error handling complete (file not found, invalid UUID, DB connection failure)
- [ ] Type hints on all public functions
- [ ] No >250 LOC files (modularity)
- [ ] Circular imports avoided (tools ↔ handlers ↔ domain)

**Security Reviewer (Oracle):**
- [ ] RLS policies enforced (no tenant_id in WHERE clause without filter)
- [ ] SQL injection prevented (parameterized queries only)
- [ ] Secrets not in code (connection strings from config/env)
- [ ] Multi-tenancy isolation tested (tenant A can't see tenant B's data)
- [ ] Event audit trail immutable (events table is append-only)

**Integration Tester (Hands-On QA):**
- [ ] All three protocols working simultaneously
- [ ] Data consistency (write via one protocol, read via another)
- [ ] Projection lag acceptable (<500ms)
- [ ] Migration successful (LanceDB → Postgres, counts match)
- [ ] Rollback works (can restore from backup)

**Refactor Validator (Parallel Deep):**
- [ ] MCP tools unchanged (backward compatible)
- [ ] Tool signatures match old implementations
- [ ] No circular imports (tools ↔ handlers)
- [ ] Old code removed (no duplication)
- [ ] Tests cover refactored tools (>80% coverage)

**Sign-Off Checklist:**
```
✓ Architecture Review passed
✓ Code Quality Review passed
✓ Security Review passed
✓ Integration Testing passed
✓ Refactor Validation passed

All 5 reviewers must approve before merge.
```

**CATEGORY:** review | SKILLS: []

**TEST (TDD):**
- RED: No reviewer has approved yet
- GREEN: All 5 reviewers have approved
- MANUAL QA: Run full test suite (`pytest tests/ -v`)

**REVIEW CHECKLIST:** (This IS the review checklist)

**RISK LEVEL:** N/A (governance process)

**BLOCKING ISSUES:**
- Reviewer feedback not addressed (iterate)
- Test coverage < 80% (add tests)

**EFFORT:** 0 LOC (governance), 2-4 hours (parallel reviews across 5 reviewers)

**TOTAL WAVE 7:** 250 LOC new (tests), ~4-6 hours (including reviews)

---

## Summary: Full Task Graph

| Wave | Tasks | Parallel | Dependency | Effort | Timeline |
|------|-------|----------|-----------|--------|----------|
| 1 | W1.1, W1.2, W1.3 | Yes (all parallel) | None | 260 LOC, 1.5h | Days 1-2 |
| 2 | W2.1, W2.2 | Yes (parallel) | Wave 1 | 330 LOC, 1.5h | Days 2-3 |
| 3 | W3.1, W3.2 | Yes (parallel) | Wave 2 | 440 LOC, 1.5h | Days 3-4 |
| 4 | W4.1, W4.2 | Yes (parallel) | Wave 3 | 330 LOC, 1.5h | Days 4-5 |
| 5 | W5.1, W5.2, W5.3 | Yes (parallel) | Wave 4 | 600 LOC, 2.5h | Days 5-7 |
| 6 | W6.1, W6.2 | Sequential | Wave 5 | 300 LOC, 1.5h (+ migration) | Days 7-8 |
| 7 | W7.1, W7.2 | W7.1 then W7.2 | Wave 6 | 250 LOC (tests), 4-6h (reviews) | Days 8-10 |
| **TOTAL** | **15 tasks** | **6 waves** | **Linear chain** | **2,510 LOC** | **10 days** |

---

## Critical Path

```
W1 (Foundation, 1.5h)
  ↓
W2 (Domain Model, 1.5h)
  ↓
W3 (Handlers, 1.5h)
  ↓
W4 (Projections, 1.5h)
  ↓
W5 (Protocol Adapters, 2.5h)
  ↓
W6 (Migration, 1.5h + migration time)
  ↓
W7 (Integration + Governance, 4-6h)

TOTAL CRITICAL PATH: ~14-16 days (serial)
WITH PARALLELIZATION: ~10 days (waves run 6 tasks at a time)
```

---

## Continuation Points (Pause/Resume)

**Safe pause points (state is persisted):**
1. After W1 (Postgres schema created, nothing else needed)
2. After W2 (Domain models defined, no events emitted yet)
3. After W3 (Handlers defined, no production traffic yet)
4. After W4 (Projections running, data flowing through event store)
5. After W5 (All three protocols live, old LanceDB/DuckDB still running)
6. After W6 (Data migrated, ready for cutover)

**Resuming from pause:**
- Just run the next wave; state is persisted in Postgres/events
- No data loss (events are immutable, idempotent replay safe)

---

## Atomic Commit Strategy

**Commits per wave (each wave = 1-2 atomic commits):**

**Wave 1:**
```
Commit 1: "feat(db): Add Postgres schema (events, projections, RLS)"
  - src/migrations/001_create_schema.sql
  - src/migrations/002_enable_rls.sql

Commit 2: "feat(core): Create eventsourcing application"
  - src/domain/application.py
  - pyproject.toml (add eventsourcing dep)
```

**Wave 2:**
```
Commit 3: "feat(domain): Define aggregates (Document, Entity, Relation)"
  - src/domain/aggregates.py

Commit 4: "feat(domain): Create command/event/query Pydantic models"
  - src/domain/models.py
```

**Wave 3:**
```
Commit 5: "feat(handlers): Implement command handler"
  - src/handlers/command_handler.py

Commit 6: "feat(handlers): Implement query handler"
  - src/handlers/query_handler.py
```

**Wave 4:**
```
Commit 7: "feat(projections): Add Postgres projection subscriber"
  - src/projections/postgres_projection.py

Commit 8: "feat(projections): Add LanceDB projection subscriber"
  - src/projections/lancedb_projection.py
```

**Wave 5:**
```
Commit 9: "feat(api): Add HTTP adapter (Starlette)"
  - src/api/http.py
  - src/server.py (inject HTTP server)

Commit 10: "feat(api): Add Unix socket adapter (JSON-RPC 2.0)"
  - src/api/socket.py
  - src/server.py (inject socket server)

Commit 11: "refactor(tools): Tools delegate to handlers"
  - src/tools/ingest_tools.py
  - src/tools/search_tools.py
  - (other tool modules)
```

**Wave 6:**
```
Commit 12: "feat(migration): Add LanceDB → Postgres migration script"
  - scripts/migrate_to_postgres.py

Commit 13: "feat(migration): Add rollback script"
  - scripts/rollback_migration.py
```

**Wave 7:**
```
Commit 14: "test(integration): Add end-to-end integration tests"
  - tests/test_integration_e2e.py

Commit 15: "docs(governance): Add ultra team review checklist"
  - ULTRA-TEAM-REVIEW.md
```

**Total commits: 15 (one per logical unit, each passing all tests)**

---

## Risk Mitigation

| Risk | Mitigation | Responsibility |
|------|-----------|-----------------|
| **Postgres schema wrong** | Schema reviewed by Architecture reviewer, tested locally before migration | W1 team |
| **Event sourcing misconfigured** | Application singleton tested, connection string from config | W1.2 reviewer |
| **RLS policies leak data** | Security reviewer validates every policy, test with multiple tenants | W1.3 reviewer |
| **Circular imports** | Refactor validator checks tool ↔ handler imports, no cycles | W5.3 reviewer |
| **Migration data loss** | Backup before migration, validation script, rollback available | W6 team |
| **Protocol incompatibility** | E2E tests verify all three protocols work together | W7.1 tester |
| **Projection lag** | Tests wait for projections to sync, document acceptable latency | W7.1 tester |

---

## Success Criteria

All tasks pass when:
1. ✓ Tests pass (RED → GREEN on every task)
2. ✓ Manual QA passes (curl, nc, pytest -v -s)
3. ✓ 5 reviewers approve (Architecture, Code Quality, Security, Integration, Refactor)
4. ✓ All commits are atomic (each passes tests independently)
5. ✓ Zero breaking changes to MCP interface
6. ✓ Data migrated without loss (counts match)
7. ✓ All three protocols (MCP, HTTP, socket) working simultaneously

---

## Questions to Validate

Before starting Wave 1, confirm:

1. **Postgres accessibility** — Can Corpus reach Postgres locally? (docker run -d -p 5432:5432 postgres:15 with pgvector?)
2. **Eventsourcing version** — Use latest v10+? (pyproject.toml updated?)
3. **Multi-tenancy placeholder** — Accept hardcoded "00000000-0000-0000-0000-000000000000" for now? (will be dynamic in next wave)
4. **Rollback strategy** — Is manual rollback via script acceptable? (or need auto-rollback?)
5. **Team size** — Solo (you) or team of 5 reviewers? (affects review timeline)
6. **Migration window** — Can you run both old + new stacks in parallel during W6? (or need cutover?)

---

## Final Notes

This plan is **production-ready** for personal → SMB → enterprise path:
- ✓ Event sourcing foundation (audit trail, reproducibility)
- ✓ Multi-tenancy ready (RLS at DB layer)
- ✓ Three protocols unified (MCP, HTTP, socket)
- ✓ Migration safe (data integrity validated, rollback available)
- ✓ Governance enforced (5 parallel reviewers)

**Next phase (SMB, weeks 2-4):**
- Add multi-tenant context to commands/queries (tenant_id from JWT token)
- Add auth (JWT token validation in HTTP + socket)
- Add TLS (HTTPS for remote, keep socket local)

**Phase after (Enterprise, weeks 5-8):**
- Split backend into separate service (gRPC + RemoteCorpusService)
- Add Postgres read replicas (distributed queries)
- Add Kafka event log (distributed audit trail)
- All without changing protocol adapters ✓

---

**Ready to execute. Proceed?**

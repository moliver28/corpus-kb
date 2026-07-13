# Corpus-KB Dual-Protocol Refactor: W1 & W2 Precise Line Edits

**Target Audience:** Junior developer with Python 3.11+ experience  
**Format:** Exact line edits (old_string → new_string) for each file modification  
**Scope:** WAVE 1 (Foundation) + WAVE 2 (Domain Model)  
**Total Effort:** ~2.5 hours (dev) + 1 hour (review)

---

## WAVE 1: Foundation (Postgres + Eventsourcing)

### W1.1: Create Postgres Schema

**Status:** NEW FILE  
**Path:** `src/migrations/001_create_schema.sql`  
**Purpose:** Event store + projection tables + RLS infrastructure  
**Dependencies:** Postgres 13+, pgvector extension, pgcrypto extension

**File Content:**

```sql
-- src/migrations/001_create_schema.sql
-- Corpus-KB Event Sourcing Schema
-- Run with: psql corpus-kb < src/migrations/001_create_schema.sql

-- ===== Extensions =====
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ===== Tenants Table (Multi-Tenancy) =====
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===== Event Store (Source of Truth) =====
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

-- ===== Projection Checkpoints (Catch-up Subscriptions) =====
CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name TEXT NOT NULL,
    tenant_id UUID NOT NULL,
    last_event_id UUID,
    checkpoint_version INT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (projection_name, tenant_id),
    CONSTRAINT fk_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- ===== Documents Projection =====
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

-- ===== Chunks Projection (with Vectors) =====
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

-- ===== Entities Projection =====
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

-- ===== Relations Projection =====
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

-- ===== Full-Text Search Index =====
CREATE INDEX idx_chunks_text_fts ON chunks USING gin(to_tsvector('english', text));
```

**Imports Needed:** None (SQL DDL file)

**Files That Depend on This:**
- `src/domain/application.py` (W1.2) — expects events table to exist
- `src/handlers/query_handler.py` (W3.2) — queries projection tables
- `src/projections/postgres_projection.py` (W4.1) — writes to projection tables

**Test Command:**
```bash
psql corpus-kb -c "\dt" | grep -E "events|documents|chunks|entities|relations"
# Should list all 6 tables
```

---

### W1.2: Create Eventsourcing Application

**Status:** NEW FILE  
**Path:** `src/domain/application.py`  
**Purpose:** Wire up eventsourcing library + Postgres backend  
**Dependencies:** eventsourcing >= 10.0, Postgres connection string in config

**File Content:**

```python
# src/domain/application.py
"""Event sourcing application for Corpus-KB.

This module initializes the eventsourcing Application with Postgres backend.
All domain aggregates save their events to the event store via this application.
"""

from eventsourcing.application import Application
from eventsourcing.domain import AggregateCreated
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class CorpusApplication(Application):
    """Event sourcing application for Corpus-KB.
    
    Manages event store (Postgres), event bus, and aggregate persistence.
    Singleton per process.
    """
    
    def __init__(self, config: dict):
        """Initialize application with Postgres backend.
        
        Args:
            config: Configuration dict with database connection string
                   Expected: config["database"]["connection_string"]
        """
        # Configure Postgres backend via environment variables
        # eventsourcing reads these to set up infrastructure
        env = {
            "INFRASTRUCTURE_FACTORY": "eventsourcing.postgres:Factory",
            "DATABASES_CONN_STR": config["database"]["connection_string"],
            "DATABASES_ECHO": False,  # Set to True for SQL debugging
        }
        
        super().__init__(env=env)
        self.config = config
        logger.info("CorpusApplication initialized with Postgres backend")


# Global application instance (singleton per process)
_app: Optional[CorpusApplication] = None


def get_app() -> CorpusApplication:
    """Get or create the global application instance.
    
    Returns:
        CorpusApplication: Singleton instance
        
    Raises:
        RuntimeError: If config cannot be loaded
    """
    global _app
    if _app is None:
        from config import load_config
        config = load_config()
        _app = CorpusApplication(config)
    return _app
```

**Imports Needed:**
- `eventsourcing.application.Application`
- `eventsourcing.domain.AggregateCreated`
- `typing.Optional`
- `logging`

**Files That Depend on This:**
- `src/domain/aggregates.py` (W2.1) — uses get_app() to save aggregates
- `src/handlers/command_handler.py` (W3.1) — uses get_app() to access event store
- `src/projections/postgres_projection.py` (W4.1) — uses get_app() to subscribe to events
- `src/projections/lancedb_projection.py` (W4.2) — uses get_app() to subscribe to events

**Test Command:**
```bash
python -c "from src.domain.application import get_app; app = get_app(); print(f'App initialized: {app}')"
# Should print: App initialized: <CorpusApplication object at ...>
```

---

### W1.3: Design RLS Policies

**Status:** NEW FILE  
**Path:** `src/migrations/002_enable_rls.sql`  
**Purpose:** Enable Row-Level Security for multi-tenancy  
**Dependencies:** Postgres 13+, tables from W1.1 must exist

**File Content:**

```sql
-- src/migrations/002_enable_rls.sql
-- Row-Level Security (RLS) Policies for Multi-Tenancy
-- Run after 001_create_schema.sql
-- Run with: psql corpus-kb < src/migrations/002_enable_rls.sql

-- ===== Step 1: Enable RLS on All Tables =====
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE relations ENABLE ROW LEVEL SECURITY;

-- ===== Step 2: Create RLS Policies (Filter by current_setting('app.tenant_id')) =====

-- Tenants policy
CREATE POLICY tenants_tenant_isolation ON tenants
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Events policy
CREATE POLICY events_tenant_isolation ON events
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Projection checkpoints policy
CREATE POLICY projection_checkpoints_tenant_isolation ON projection_checkpoints
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Documents policy
CREATE POLICY documents_tenant_isolation ON documents
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Chunks policy
CREATE POLICY chunks_tenant_isolation ON chunks
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Entities policy
CREATE POLICY entities_tenant_isolation ON entities
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- Relations policy
CREATE POLICY relations_tenant_isolation ON relations
    FOR ALL USING (tenant_id = (current_setting('app.tenant_id')::uuid))
    WITH CHECK (tenant_id = (current_setting('app.tenant_id')::uuid));

-- ===== Step 3: Create Roles =====

-- Admin role (bypasses RLS, for migrations)
CREATE ROLE corpus_admin SUPERUSER;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO corpus_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO corpus_admin;

-- User role (filtered by RLS)
CREATE ROLE corpus_user;
GRANT USAGE ON SCHEMA public TO corpus_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO corpus_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO corpus_user;
```

**Imports Needed:** None (SQL DDL file)

**Files That Depend on This:**
- `src/handlers/query_handler.py` (W3.2) — sets `app.tenant_id` before queries
- `src/projections/postgres_projection.py` (W4.1) — writes with tenant_id from events

**Test Command:**
```bash
psql corpus-kb -c "\d documents" | grep -i "policies"
# Should show: Policies: documents_tenant_isolation
```

---

## WAVE 2: Domain Model (DDD Aggregates)

### W2.1: Define Domain Aggregates

**Status:** NEW FILE  
**Path:** `src/domain/aggregates.py`  
**Purpose:** Event-decorated aggregates (Document, Entity, Relation)  
**Dependencies:** eventsourcing >= 10.0, dataclasses, uuid

**File Content:**

```python
# src/domain/aggregates.py
"""Domain aggregates for Corpus-KB.

Aggregates are the root entities in Domain-Driven Design.
Each aggregate is a cluster of domain objects (entities and value objects)
that can be treated as a single unit.

Aggregates use the @event decorator to emit domain events.
Events are the source of truth; aggregate state is derived from events.
"""

from eventsourcing.domain import Aggregate, event
from dataclasses import dataclass, field
from uuid import UUID
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class Document(Aggregate):
    """A document aggregate — source of truth for a single ingested file/text.
    
    Invariants:
    - tenant_id is immutable (set at creation, never changed)
    - source is immutable (identifies the document)
    - chunk_count >= 0
    """
    
    tenant_id: UUID
    source: str
    source_type: str = "text"  # file, text, url
    chunk_count: int = 0
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    language: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @event("Inserted")
    def ingest(self, source: str, source_type: str, metadata: Dict[str, Any]):
        """Ingest a document — fires DocumentIngested event.
        
        Args:
            source: File path or text identifier
            source_type: "file", "text", or "url"
            metadata: Additional metadata (file_size, language, etc.)
        """
        self.source = source
        self.source_type = source_type
        self.metadata = metadata
        logger.debug(f"Document {self.id} ingested: {source}")
    
    @event("ChunksAdded")
    def add_chunks(self, chunk_count: int):
        """Add chunks to document.
        
        Args:
            chunk_count: Number of chunks created from this document
        """
        self.chunk_count = chunk_count
        logger.debug(f"Document {self.id} now has {chunk_count} chunks")


@dataclass
class Entity(Aggregate):
    """An entity in the knowledge graph.
    
    Entities represent concepts, people, places, classes, functions, etc.
    
    Invariants:
    - tenant_id is immutable
    - name is immutable (identifies the entity within a tenant)
    - entity_type is immutable
    """
    
    tenant_id: UUID
    name: str
    entity_type: str = "concept"  # Person, Place, Class, Function, Concept
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @event("Created")
    def create(self, name: str, entity_type: str, metadata: Dict[str, Any]):
        """Create an entity.
        
        Args:
            name: Entity name (e.g., "authenticate_user", "User class")
            entity_type: Type of entity (Person, Place, Class, Function, Concept)
            metadata: Additional metadata
        """
        self.name = name
        self.entity_type = entity_type
        self.metadata = metadata
        logger.debug(f"Entity {self.id} created: {name} ({entity_type})")


@dataclass
class Relation(Aggregate):
    """A relation between two entities.
    
    Relations represent typed connections in the knowledge graph.
    
    Invariants:
    - tenant_id is immutable
    - source_entity_id and target_entity_id are immutable
    - relation_type is immutable
    """
    
    tenant_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"  # CONTAINS, DEPENDS_ON, CALLS, KNOWS
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @event("Created")
    def create(
        self,
        source_id: UUID,
        target_id: UUID,
        rel_type: str,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Create a relation.
        
        Args:
            source_id: Source entity ID
            target_id: Target entity ID
            rel_type: Type of relation (CONTAINS, DEPENDS_ON, CALLS, KNOWS)
            weight: Strength of relation (default 1.0)
            metadata: Additional metadata
        """
        self.source_entity_id = source_id
        self.target_entity_id = target_id
        self.relation_type = rel_type
        self.weight = weight
        self.metadata = metadata or {}
        logger.debug(
            f"Relation {self.id} created: {source_id} --{rel_type}--> {target_id}"
        )
```

**Imports Needed:**
- `eventsourcing.domain.Aggregate, event`
- `dataclasses.dataclass, field`
- `uuid.UUID`
- `typing.Optional, List, Dict, Any`
- `logging`

**Files That Depend on This:**
- `src/handlers/command_handler.py` (W3.1) — instantiates aggregates
- `src/projections/postgres_projection.py` (W4.1) — reads event payloads from aggregates

**Test Command:**
```bash
python -c "
from src.domain.aggregates import Document, Entity, Relation
from uuid import uuid4

tenant_id = uuid4()
doc = Document(tenant_id=tenant_id, source='test.py')
print(f'Document created: {doc.id}')
"
# Should print: Document created: <uuid>
```

---

### W2.2: Create Pydantic Command/Event Models

**Status:** NEW FILE  
**Path:** `src/domain/models.py`  
**Purpose:** Command and Event envelopes (request/response serialization)  
**Dependencies:** pydantic >= 2.0, uuid, datetime, enum

**File Content:**

```python
# src/domain/models.py
"""Pydantic models for commands, events, and queries.

Commands are write requests (imperative).
Events are write results (past tense, immutable).
Queries are read requests.
Results are read responses.

All models are JSON-serializable for HTTP/socket transport.
"""

from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from datetime import datetime
from typing import Optional, List, Any, Dict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# ===== Commands (Write Requests) =====

class DomainCommand(BaseModel):
    """Base command envelope.
    
    All commands must include:
    - tenant_id: Which tenant this command belongs to
    - command_id: Unique command identifier (for idempotency)
    """
    
    tenant_id: UUID
    command_id: UUID = Field(default_factory=uuid4)
    
    class Config:
        json_schema_extra = {
            "example": "All commands inherit from DomainCommand"
        }


class IngestFileCommand(DomainCommand):
    """Ingest a file into the knowledge base.
    
    Either provide file_path (read from disk) or content (already read).
    """
    
    file_path: str
    content: Optional[str] = None  # if None, read from disk
    source_type: Optional[str] = None  # auto-detect if None


class IngestTextCommand(DomainCommand):
    """Ingest raw text into the knowledge base."""
    
    text: str
    source_type: str = "text"


class AddEntityCommand(DomainCommand):
    """Add an entity to the knowledge graph."""
    
    name: str
    entity_type: str = "concept"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AddRelationCommand(DomainCommand):
    """Add a relation between two entities."""
    
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = "related_to"
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ===== Events (Write Results) =====

class DomainEvent(BaseModel):
    """Base event envelope.
    
    Events are immutable records of what happened.
    All events include:
    - event_id: Unique event identifier
    - tenant_id: Which tenant this event belongs to
    - aggregate_id: Which aggregate this event affects
    - aggregate_type: Type of aggregate (Document, Entity, Relation)
    - event_type: Type of event (DocumentIngested, EntityAdded, etc.)
    - payload: Event-specific data (JSON)
    - version: Aggregate version after this event
    - created_at: When the event occurred
    """
    
    event_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    aggregate_id: UUID
    aggregate_type: str  # "Document", "Entity", "Relation"
    event_type: str      # "DocumentIngested", "EntityAdded", etc.
    payload: Dict[str, Any]
    version: int = 1
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    
    class Config:
        json_schema_extra = {
            "example": "Events are immutable records of domain changes"
        }


class DocumentIngestedEvent(DomainEvent):
    """Event: A document was ingested."""
    
    aggregate_type: str = "Document"
    event_type: str = "DocumentIngested"
    # payload: {source, source_type, chunk_count, metadata}


class EntityAddedEvent(DomainEvent):
    """Event: An entity was added to the graph."""
    
    aggregate_type: str = "Entity"
    event_type: str = "EntityAdded"
    # payload: {name, entity_type, metadata}


class RelationAddedEvent(DomainEvent):
    """Event: A relation was added to the graph."""
    
    aggregate_type: str = "Relation"
    event_type: str = "RelationAdded"
    # payload: {source_entity_id, target_entity_id, relation_type, weight, metadata}


# ===== Queries (Read Requests) =====

class DomainQuery(BaseModel):
    """Base query envelope.
    
    All queries must include tenant_id for multi-tenancy.
    """
    
    tenant_id: UUID


class SearchQuery(DomainQuery):
    """Search for chunks by semantic similarity."""
    
    query: str
    k: int = 10  # number of results
    source_type: Optional[str] = None  # filter by source type


class SQLQuery(DomainQuery):
    """Execute a parameterized SQL query."""
    
    sql: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ListDocumentsQuery(DomainQuery):
    """List documents with pagination."""
    
    limit: int = 100
    offset: int = 0


class ListEntitiesQuery(DomainQuery):
    """List entities with optional filtering."""
    
    entity_type: Optional[str] = None
    limit: int = 100
    offset: int = 0


class GetEntityRelationsQuery(DomainQuery):
    """Get all relations for an entity."""
    
    entity_id: UUID


# ===== Results (Read Results) =====

class SearchResult(BaseModel):
    """Result of a search query."""
    
    chunk_id: UUID
    text: str
    score: float  # 0.0 to 1.0
    source: str
    doc_id: UUID
    chunk_type: Optional[str] = None
    entity_name: Optional[str] = None


class DocumentResult(BaseModel):
    """Result of a list documents query."""
    
    doc_id: UUID
    source: str
    source_type: str
    chunk_count: int
    created_at: str


class EntityResult(BaseModel):
    """Result of a list entities query."""
    
    entity_id: UUID
    name: str
    entity_type: str
    metadata: Dict[str, Any]


class RelationResult(BaseModel):
    """Result of a get relations query."""
    
    relation_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str
    weight: float


class CommandResult(BaseModel):
    """Result of a command execution."""
    
    status: str  # "success" or "error"
    command_id: UUID
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class QueryResult(BaseModel):
    """Result of a query execution."""
    
    status: str  # "success" or "error"
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
```

**Imports Needed:**
- `pydantic.BaseModel, Field`
- `uuid.UUID, uuid4`
- `datetime.datetime`
- `typing.Optional, List, Any, Dict`
- `enum.Enum`
- `logging`

**Files That Depend on This:**
- `src/handlers/command_handler.py` (W3.1) — receives commands, returns results
- `src/handlers/query_handler.py` (W3.2) — receives queries, returns results
- `src/api/http.py` (W5.1) — serializes models to JSON
- `src/api/socket.py` (W5.2) — serializes models to JSON

**Test Command:**
```bash
python -c "
from src.domain.models import IngestFileCommand, SearchQuery
from uuid import uuid4

cmd = IngestFileCommand(tenant_id=uuid4(), file_path='test.py')
print(f'Command JSON: {cmd.model_dump_json()}')

query = SearchQuery(tenant_id=uuid4(), query='test')
print(f'Query JSON: {query.model_dump_json()}')
"
# Should print JSON serializations
```

---

## Summary: W1 + W2 Deliverables

### Files Created (NEW)

| File | LOC | Purpose |
|------|-----|---------|
| `src/migrations/001_create_schema.sql` | 120 | Event store + projection tables |
| `src/migrations/002_enable_rls.sql` | 80 | RLS policies for multi-tenancy |
| `src/domain/application.py` | 60 | Eventsourcing application setup |
| `src/domain/aggregates.py` | 180 | Document, Entity, Relation aggregates |
| `src/domain/models.py` | 250 | Pydantic command/event/query models |
| **TOTAL** | **690** | **Foundation + Domain Model** |

### Files Modified (MOD)

| File | Changes |
|------|---------|
| `pyproject.toml` | Add eventsourcing, sqlalchemy, starlette dependencies |
| `config.yaml` | Add database connection string |

### Import Dependencies

**New imports to add to pyproject.toml:**

```toml
dependencies = [
    # ... existing ...
    "eventsourcing>=10.0",      # Event sourcing framework
    "sqlalchemy>=2.0",          # SQL toolkit
    "starlette>=0.35",          # ASGI web framework
    "uvicorn>=0.24",            # ASGI server
    "psycopg2-binary>=2.9",     # Postgres driver
]
```

### Dependency Graph

```
W1.1 (Schema)
  ↓
W1.2 (Application) ← depends on W1.1
  ↓
W1.3 (RLS) ← depends on W1.1
  ↓
W2.1 (Aggregates) ← depends on W1.2
  ↓
W2.2 (Models) ← depends on W2.1
```

### Critical Path

1. **W1.1** → Create schema (SQL DDL)
2. **W1.2** → Create application (Python, uses W1.1)
3. **W1.3** → Enable RLS (SQL DDL, uses W1.1)
4. **W2.1** → Define aggregates (Python, uses W1.2)
5. **W2.2** → Create models (Python, uses W2.1)

All W1 tasks can run in parallel (no code dependencies).  
All W2 tasks can run in parallel (both depend on W1).

### Testing Strategy

**W1.1 (Schema):**
```bash
psql corpus-kb -c "\dt" | grep -E "events|documents|chunks|entities|relations"
```

**W1.2 (Application):**
```bash
python -c "from src.domain.application import get_app; app = get_app(); print('OK')"
```

**W1.3 (RLS):**
```bash
psql corpus-kb -c "\d documents" | grep -i "policies"
```

**W2.1 (Aggregates):**
```bash
python -c "from src.domain.aggregates import Document; print('OK')"
```

**W2.2 (Models):**
```bash
python -c "from src.domain.models import IngestFileCommand; cmd = IngestFileCommand(tenant_id='...', file_path='test.py'); print(cmd.model_dump_json())"
```

---

## Next Steps (W3 & Beyond)

After W1 + W2 are complete:

- **W3.1** → CommandHandler (uses W2.1 + W2.2)
- **W3.2** → QueryHandler (uses W2.2)
- **W4.1** → Postgres Projections (uses W3.1 + W3.2)
- **W4.2** → LanceDB Projections (uses W3.1 + W3.2)
- **W5.1** → HTTP Adapter (uses W4.1 + W4.2)
- **W5.2** → Socket Adapter (uses W4.1 + W4.2)
- **W5.3** → MCP Adapter Refactor (uses W4.1 + W4.2)

---

## Notes for Junior Developer

### Key Concepts

1. **Event Sourcing:** Instead of storing current state, store all events that led to that state. Replay events to reconstruct state.

2. **Aggregates:** Root entities that enforce business rules. Each aggregate has a unique ID and can emit events.

3. **Projections:** Read models built from events. Eventual consistency (events → projections → queries).

4. **Multi-Tenancy:** Every table has `tenant_id`. RLS policies filter by `current_setting('app.tenant_id')`.

5. **Pydantic Models:** Serializable data classes. Use for HTTP/socket transport.

### Common Pitfalls

- **Don't modify aggregates directly.** Use `@event` decorated methods.
- **Don't forget tenant_id.** Every command, event, and query must include it.
- **Don't skip RLS.** Set `app.tenant_id` before every query.
- **Don't hardcode UUIDs.** Use `uuid4()` for new IDs.

### Debugging Tips

- Check event store: `SELECT * FROM events WHERE tenant_id = '...' ORDER BY created_at DESC;`
- Check projections: `SELECT * FROM documents WHERE tenant_id = '...';`
- Check RLS: `SET app.tenant_id = '...'; SELECT * FROM documents;`
- Check aggregates: `python -c "from src.domain.aggregates import Document; print(Document.__dict__)"`


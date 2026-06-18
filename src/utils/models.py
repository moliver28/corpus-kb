"""Pydantic models for all MCP tool I/O and internal data structures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Internal data models (used across storage, chunking, RAG layers)
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """A single chunk of content, after splitting and before embedding."""
    chunk_id: str = field(default_factory=_uuid)
    doc_id: str = ""
    text: str = ""
    vector: Optional[list[float]] = None
    chunk_index: int = 0
    source: str = ""
    source_type: str = "text"          # text | code | markdown
    metadata: dict = field(default_factory=dict)
    # Hierarchy fields
    heading_path: list[str] = field(default_factory=list)
    parent_chunk_id: Optional[str] = None
    sibling_order: int = 0
    sibling_count: int = 0
    scope_chain: list[str] = field(default_factory=list)
    chunk_type: str = "paragraph"      # function | class | method | section | paragraph
    entity_name: Optional[str] = None  # Function/class name if code
    file_path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    created_at: str = field(default_factory=_now)

    def to_lance(self) -> dict:
        d = asdict(self)
        # Ensure metadata is JSON string if it's a dict
        if isinstance(d.get("metadata"), dict):
            d["metadata"] = json.dumps(d["metadata"])
        return d

    @classmethod
    def from_lance(cls, data: dict) -> "Chunk":
        if isinstance(data.get("metadata"), str):
            data["metadata"] = json.loads(data["metadata"])
        if isinstance(data.get("heading_path"), str):
            data["heading_path"] = json.loads(data["heading_path"])
        if isinstance(data.get("scope_chain"), str):
            data["scope_chain"] = json.loads(data["scope_chain"])
        # Remove lanceDB internal fields
        data.pop("_distance", None)
        data.pop("_relevance", None)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Document:
    """Document-level metadata."""
    doc_id: str = field(default_factory=_uuid)
    source: str = ""
    source_type: str = "text"          # file | text | url | transcript
    metadata: dict = field(default_factory=dict)
    chunk_count: int = 0
    created_at: str = field(default_factory=_now)

    def to_lance(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("metadata"), dict):
            d["metadata"] = json.dumps(d["metadata"])
        return d


@dataclass
class SearchResult:
    """A single search result with metadata."""
    chunk_id: str
    text: str
    score: float
    source: str
    doc_id: str = ""
    chunk_type: str = "paragraph"
    entity_name: Optional[str] = None
    heading_path: list[str] = field(default_factory=list)
    scope_chain: list[str] = field(default_factory=list)
    file_path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    context_type: str = "direct"       # direct | parent | sibling
    metadata: dict = field(default_factory=dict)


@dataclass
class Entity:
    """A node in the entity graph."""
    entity_id: str = field(default_factory=_uuid)
    name: str = ""
    type: str = "concept"             # Person | Place | Class | Function | Concept | etc.
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)


@dataclass
class Relation:
    """An edge in the entity graph."""
    relation_id: str = field(default_factory=_uuid)
    source_id: str = ""
    target_id: str = ""
    relation_type: str = "related_to"  # CONTAINS | DEPENDS_ON | CALLS | KNOWS | etc.
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)


@dataclass
class Version:
    """A table version entry (from LanceDB)."""
    version: int
    timestamp: str
    tag: Optional[str] = None


@dataclass
class Branch:
    """A named branch."""
    name: str
    version: int
    created_at: str = field(default_factory=_now)


@dataclass
class Stats:
    """Database statistics."""
    total_documents: int = 0
    total_chunks: int = 0
    total_entities: int = 0
    total_relations: int = 0
    db_size_bytes: int = 0
    current_version: int = 0
    storage_path: str = ""

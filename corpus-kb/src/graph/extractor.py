"""Entity extraction from text and markdown.

Extracts entities (concepts, classes, functions, etc.) from markdown and text chunks
using regex patterns. Returns Entity objects with name, type, and source_type.
"""

from __future__ import annotations

import re
from typing import Optional

from ..utils.models import Entity


# ============================================================================
# Entity Type Patterns
# ============================================================================

# Markdown heading pattern: # Title, ## Subtitle, ### Sub-subtitle
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)(?:\s*\{.*\})?$", re.MULTILINE)

# CamelCase pattern: UserService, AuthManager, etc.
CAMEL_CASE_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)*)\b")

# Code-like identifiers: function_name, variable_name
SNAKE_CASE_PATTERN = re.compile(r"\b([a-z_][a-z0-9_]*)\b")

# Common concept keywords in markdown
CONCEPT_KEYWORDS = {
    "concept",
    "definition",
    "principle",
    "pattern",
    "architecture",
    "design",
    "algorithm",
    "data structure",
    "framework",
    "library",
    "module",
    "component",
    "service",
    "api",
    "protocol",
    "standard",
}


# ============================================================================
# Entity Extraction Functions
# ============================================================================


def extract_entities(
    text: str,
    source_type: str = "text",
    source_document_id: Optional[str] = None,
) -> list[Entity]:
    """Extract entities from text or markdown.

    Args:
        text: The text to extract entities from.
        source_type: "code" | "markdown" | "text"
        source_document_id: Optional document ID for metadata.

    Returns:
        List of Entity objects with name, type, and metadata.
    """
    entities: dict[str, Entity] = {}  # Use dict to deduplicate by name

    if source_type == "markdown":
        entities.update(_extract_markdown_entities(text, source_document_id))
    elif source_type == "code":
        entities.update(_extract_code_entities(text, source_document_id))
    else:  # "text"
        entities.update(_extract_text_entities(text, source_document_id))

    return list(entities.values())


def _extract_markdown_entities(
    text: str,
    source_document_id: Optional[str] = None,
) -> dict[str, Entity]:
    """Extract entities from markdown (headings, concepts)."""
    entities: dict[str, Entity] = {}

    # Extract headings as CONCEPT entities
    for match in MARKDOWN_HEADING_PATTERN.finditer(text):
        level = len(match.group(1))  # Number of # symbols
        heading_text = match.group(2).strip()

        if heading_text:
            entity = Entity(
                name=heading_text,
                entity_type="CONCEPT",
                source_type="markdown",
                source_document_id=source_document_id,
                metadata={"heading_level": level},
            )
            entities[heading_text] = entity

    # Extract CamelCase identifiers as potential concepts
    for match in CAMEL_CASE_PATTERN.finditer(text):
        name = match.group(1)
        if len(name) > 2 and name not in entities:  # Skip short names
            entity = Entity(
                name=name,
                entity_type="CONCEPT",
                source_type="markdown",
                source_document_id=source_document_id,
                metadata={"pattern": "CamelCase"},
            )
            entities[name] = entity

    return entities


def _extract_code_entities(
    text: str,
    source_document_id: Optional[str] = None,
) -> dict[str, Entity]:
    """Extract entities from code (class names, function names)."""
    entities: dict[str, Entity] = {}

    # Extract CamelCase identifiers (likely class names)
    for match in CAMEL_CASE_PATTERN.finditer(text):
        name = match.group(1)
        if len(name) > 2 and name not in entities:
            entity = Entity(
                name=name,
                entity_type="CLASS",
                source_type="code",
                source_document_id=source_document_id,
                metadata={"pattern": "CamelCase"},
            )
            entities[name] = entity

    # Extract snake_case identifiers (likely function/variable names)
    for match in SNAKE_CASE_PATTERN.finditer(text):
        name = match.group(1)
        if (
            len(name) > 2
            and name not in entities
            and not name.startswith("_")
            and name not in {"def", "class", "import", "from", "return", "if", "else"}
        ):
            entity = Entity(
                name=name,
                entity_type="FUNCTION",
                source_type="code",
                source_document_id=source_document_id,
                metadata={"pattern": "snake_case"},
            )
            entities[name] = entity

    return entities


def _extract_text_entities(
    text: str,
    source_document_id: Optional[str] = None,
) -> dict[str, Entity]:
    """Extract entities from plain text (concepts, named entities)."""
    entities: dict[str, Entity] = {}

    # Extract CamelCase identifiers
    for match in CAMEL_CASE_PATTERN.finditer(text):
        name = match.group(1)
        if len(name) > 2 and name not in entities:
            entity = Entity(
                name=name,
                entity_type="CONCEPT",
                source_type="text",
                source_document_id=source_document_id,
                metadata={"pattern": "CamelCase"},
            )
            entities[name] = entity

    # Extract concept keywords (e.g., "authentication", "caching")
    for keyword in CONCEPT_KEYWORDS:
        pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
        if pattern.search(text) and keyword not in entities:
            entity = Entity(
                name=keyword.title(),
                entity_type="CONCEPT",
                source_type="text",
                source_document_id=source_document_id,
                metadata={"pattern": "keyword"},
            )
            entities[keyword.title()] = entity

    return entities

"""Entity extractor — finds entities (classes, functions, named concepts) in text.

Two modes:
- regex: Pattern-based extraction for code and text
- llm: Uses Ollama for extraction (future, falls back to regex)
- hybrid: regex first, then LLM refinement (future, falls back to regex)

Currently implemented: regex mode.
"""

from __future__ import annotations

import re
from typing import Optional

# Patterns for common entity types
PATTERNS: dict[str, list[str]] = {
    "class": [
        r"(?:class|struct|interface)\s+(\w+)",              # Python/Java/C++
        r"(?:type|trait)\s+(\w+)",                            # TypeScript/Rust
        r"(?:defmodule|defstruct)\s+(\w+)",                   # Elixir
    ],
    "function": [
        r"(?:def|fn|func|fun|function)\s+(\w+)\s*\(",        # Python/Rust/Go/Kotlin/JS
        r"(?:sub)\s+(\w+)",                                   # Perl/Ruby
        r"(?:public|private|protected)?\s*(?:static)?\s*\w+\s+(\w+)\s*\(",  # Java/C# methods
    ],
    "concept": [
        r"(?:#+|##+)\s*(.+)$",                                # Markdown headings
        r"(?:^|\n)([A-Z][a-z]+(?:[A-Z][a-z]+)+)",             # CamelCase names
    ],
}


def extract_entities(
    text: str,
    source_type: str = "code",
    known_entities: Optional[set[str]] = None,
) -> list[dict]:
    """Extract entities from text using regex patterns.
    
    Args:
        text: The text to extract entities from
        source_type: "code", "markdown", or "text"
        known_entities: Set of already-known entity names to avoid duplicates
        
    Returns:
        List of dicts with keys: name, type, metadata
    """
    found = []
    seen = set(known_entities or set())
    
    # Choose patterns based on source type
    if source_type == "code":
        entity_types = ["class", "function"]
    elif source_type == "markdown":
        entity_types = ["concept"]
    else:
        entity_types = ["concept"]
    
    for ent_type in entity_types:
        if ent_type not in PATTERNS:
            continue
        for pattern in PATTERNS[ent_type]:
            for match in re.finditer(pattern, text, re.MULTILINE):
                name = match.group(1).strip()
                # Skip short names and noise
                if len(name) < 2 or name.lower() in {"the", "this", "that", "and", "for", "with"}:
                    continue
                if name not in seen:
                    seen.add(name)
                    metadata = {}
                    if ent_type == "concept":
                        # For markdown headings, add heading level info
                        heading_match = re.match(r"^(#+)\s", match.group(0))
                        if heading_match:
                            metadata["heading_level"] = len(heading_match.group(1))
                    found.append({
                        "name": name,
                        "type": ent_type.upper(),
                        "metadata": metadata,
                    })
    
    return found

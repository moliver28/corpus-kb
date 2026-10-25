"""Typed reads of nested config mappings (no ``object`` leaks).

Config blocks arrive as ``dict[str, object]``; the ``cfg.get(key, {})``
idiom leaves the section typed ``object``, so a chained ``.get`` fails
the type checker. One helper answers every nested-block read the same
way: a real mapping, or an empty one.
"""

from __future__ import annotations

from collections.abc import Mapping


def mapping_section(config: Mapping[str, object] | None, key: str) -> Mapping[str, object]:
    """The named nested mapping, or an empty one when absent or not a mapping."""
    section = (config or {}).get(key)
    return section if isinstance(section, Mapping) else {}

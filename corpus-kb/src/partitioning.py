"""Unstructured partitioning wrapper exposing typed ElementProxy objects."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4

from unstructured.partition.auto import partition as _unstructured_partition


@dataclass(frozen=True, slots=True)
class ElementProxy:
    """Typed view of an Unstructured element."""

    text: str
    element_type: str
    element_id: str
    parent_id: Optional[str] = None
    metadata: dict[str, object] = field(default_factory=dict[str, object])


def _extract_text(element: object) -> str:
    text = getattr(element, "text", None)
    return text if isinstance(text, str) else ""


def _extract_element_type(element: object) -> str:
    category = getattr(element, "category", None)
    if isinstance(category, str):
        return category
    element_type = getattr(element, "type", None)
    if isinstance(element_type, str):
        return element_type
    return "Unknown"


def _extract_element_id(element: object) -> str:
    element_id = getattr(element, "id", None)
    if isinstance(element_id, str) and element_id:
        return element_id
    element_id = getattr(element, "element_id", None)
    if isinstance(element_id, str) and element_id:
        return element_id
    return str(uuid4())


def _to_proxy(element: object) -> ElementProxy:
    """Build an ElementProxy from an Unstructured element."""
    text = _extract_text(element)
    element_type = _extract_element_type(element)
    element_id = _extract_element_id(element)

    metadata: dict[str, object] = {}
    parent_id: Optional[str] = None
    meta_obj = getattr(element, "metadata", None)
    if meta_obj is not None:
        parent_id_value = getattr(meta_obj, "parent_id", None)
        if isinstance(parent_id_value, str):
            parent_id = parent_id_value
        for key in ("coordinates", "detection_class", "page_number", "category_depth"):
            value = getattr(meta_obj, key, None)
            if value is not None:
                metadata[key] = value

    return ElementProxy(
        text=text,
        element_type=element_type,
        element_id=element_id,
        parent_id=parent_id,
        metadata=metadata,
    )


def partition(path: Path | str, strategy: str = "auto") -> list[ElementProxy]:
    """Partition *path* with Unstructured and return typed element proxies."""
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(path_obj)

    effective_strategy = _effective_strategy(strategy)
    raw_elements: list[object] = list(
        _unstructured_partition(str(path_obj), strategy=effective_strategy)
    )
    return [_to_proxy(element) for element in raw_elements]


def _effective_strategy(strategy: str) -> str:
    """Map the default strategy to a platform-safe value.

    Unstructured's ``hi_res`` strategy requires detectron2, which is not
    available on native Windows.  When the caller accepts the default
    ``auto`` strategy, prefer ``fast`` on Windows.
    """
    if strategy == "auto" and sys.platform == "win32":
        return "fast"
    return strategy

"""Typed wrappers, normalization, and fixture IO for ``langextract``."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from ..ontology import Ontology


@dataclass(frozen=True, slots=True)
class NormalizedExtraction:
    """LangExtract extraction normalized to our internal shape."""

    extraction_class: str
    extraction_text: str
    start_pos: int | None
    end_pos: int | None
    confidence: float | None
    attributes: dict[str, object] | None = None


@runtime_checkable
class CharInterval(Protocol):
    """LangExtract character interval protocol."""

    start_pos: int | None
    end_pos: int | None


@runtime_checkable
class Extraction(Protocol):
    """LangExtract extraction protocol."""

    extraction_class: str
    extraction_text: str
    char_interval: CharInterval | None
    attributes: dict[str, object] | None


@runtime_checkable
class AnnotatedDocument(Protocol):
    """LangExtract annotated document protocol."""

    extractions: list[Extraction]


class LangExtractDataModule(Protocol):
    """LangExtract ``data`` submodule protocol."""

    ExampleData: Callable[..., object]
    Extraction: Callable[..., object]
    CharInterval: Callable[..., object]


class LangExtractModule(Protocol):
    """LangExtract top-level module protocol."""

    data: LangExtractDataModule

    def extract(
        self,
        text_or_documents: str | list[str],
        prompt_description: str,
        examples: list[object],
    ) -> AnnotatedDocument | list[AnnotatedDocument]: ...


def import_langextract() -> LangExtractModule:
    """Lazily import ``langextract`` and cast to the typed Protocol."""
    return cast(LangExtractModule, importlib.import_module("langextract"))


def build_prompt_description(ontology: Ontology) -> str:
    """Build a prompt that enforces the ontology vocabulary."""
    return (
        "Extract entities from the provided text with exact character offsets. "
        f"Allowed entity types: {ontology.entity_types}. "
        "Also extract directed relations between the entities you found: for each "
        "relation, emit an extraction whose extraction_class is one of the allowed "
        "relation types below, and whose attributes dict contains 'subject' (the "
        "exact text of the source entity), 'object' (the exact text of the target "
        "entity), and 'confidence' (a float between 0 and 1). Only emit a relation "
        "when both the subject and object were also extracted as entities in the "
        "same text. "
        f"Allowed relation types: {ontology.relation_types}."
    )


def _normalize_extraction(extraction: Extraction) -> NormalizedExtraction:
    interval = extraction.char_interval
    attributes = getattr(extraction, "attributes", None)
    return NormalizedExtraction(
        extraction_class=extraction.extraction_class,
        extraction_text=extraction.extraction_text,
        start_pos=interval.start_pos if interval else None,
        end_pos=interval.end_pos if interval else None,
        confidence=None,
        attributes=attributes if isinstance(attributes, dict) else None,
    )


def _load_fixture(path: Path) -> list[NormalizedExtraction]:
    """Read one recorded ``<sha256>.jsonl`` fixture into extractions."""
    extractions: list[NormalizedExtraction] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = cast(dict[str, Any], json.loads(line))

            cls = raw.get("extraction_class")
            extraction_text = raw.get("extraction_text")
            if not isinstance(cls, str) or not isinstance(extraction_text, str):
                continue

            interval_raw = raw.get("char_interval")
            start: int | None = None
            end: int | None = None
            if isinstance(interval_raw, dict):
                interval = cast(dict[str, Any], interval_raw)
                start_raw = interval.get("start_pos")
                end_raw = interval.get("end_pos")
                if isinstance(start_raw, int) and not isinstance(start_raw, bool):
                    start = start_raw
                if isinstance(end_raw, int) and not isinstance(end_raw, bool):
                    end = end_raw

            conf_raw = raw.get("confidence")
            confidence = (
                float(conf_raw)
                if isinstance(conf_raw, (int, float)) and not isinstance(conf_raw, bool)
                else None
            )

            attrs_raw = raw.get("attributes")
            attributes = attrs_raw if isinstance(attrs_raw, dict) else None

            extractions.append(
                NormalizedExtraction(
                    extraction_class=cls,
                    extraction_text=extraction_text,
                    start_pos=start,
                    end_pos=end,
                    confidence=confidence,
                    attributes=attributes,
                )
            )
    return extractions

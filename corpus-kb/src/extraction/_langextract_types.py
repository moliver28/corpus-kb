"""Typed Protocol wrappers for the optional ``langextract`` package."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Protocol, cast, runtime_checkable

from src.ontology import Ontology


@dataclass(frozen=True, slots=True)
class NormalizedExtraction:
    """LangExtract extraction normalized to our internal shape."""

    extraction_class: str
    extraction_text: str
    start_pos: int | None
    end_pos: int | None
    confidence: float | None


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
        f"Allowed relation types: {ontology.relation_types}."
    )


def build_examples(lx: LangExtractModule, ontology: Ontology) -> list[object]:
    """Build a few example documents from the ontology."""
    examples: list[object] = []
    for entity_type in ontology.entity_types[:2]:
        extraction = lx.data.Extraction(
            extraction_class=entity_type,
            extraction_text=f"Example{entity_type}",
            char_interval=lx.data.CharInterval(start_pos=0, end_pos=10),
        )
        examples.append(
            lx.data.ExampleData(
                text=f"Example of {entity_type}.",
                extractions=[extraction],
            )
        )
    return examples

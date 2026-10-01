"""Typed Protocol wrappers for the optional ``langextract`` package."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

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

    if ontology.relation_types and len(ontology.entity_types) >= 2:
        subject_type, object_type = ontology.entity_types[0], ontology.entity_types[1]
        relation_type = ontology.relation_types[0]
        subject_text = f"Example{subject_type}"
        object_text = f"Example{object_type}"
        example_text = f"{subject_text} relates to {object_text}."
        subject_extraction = lx.data.Extraction(
            extraction_class=subject_type,
            extraction_text=subject_text,
            char_interval=lx.data.CharInterval(start_pos=0, end_pos=len(subject_text)),
        )
        object_start = example_text.index(object_text)
        object_extraction = lx.data.Extraction(
            extraction_class=object_type,
            extraction_text=object_text,
            char_interval=lx.data.CharInterval(
                start_pos=object_start, end_pos=object_start + len(object_text)
            ),
        )
        relation_extraction = lx.data.Extraction(
            extraction_class=relation_type,
            extraction_text="relates to",
            attributes={
                "subject": subject_text,
                "object": object_text,
                "confidence": 0.9,
            },
        )
        examples.append(
            lx.data.ExampleData(
                text=example_text,
                extractions=[
                    subject_extraction,
                    object_extraction,
                    relation_extraction,
                ],
            )
        )
    return examples

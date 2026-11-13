"""Typed Protocol wrappers for the ``gliner`` package (GLiNER-Relex).

``gliner`` serves ONE role here: zero-shot relation extraction with the
GLiNER-Relex checkpoint (Stepanov et al. 2026). Entities come from
``gliner2`` (``_gliner2_types.py``). Imports go through
:func:`import_gliner` with the same lazy importlib string-import pattern,
so this module imports cleanly without torch and pyright checks against
the Protocols below, never the real package.

API assumption (verified against gliner 0.2.29 on 2026-11-12):
``gliner >= 0.2`` exposes ``GLiNER.from_pretrained(model_id)`` returning
a model whose ``predict_relations(text, labels, relations, ...,
threshold, relation_threshold)`` takes ``labels`` as a mapping
``{entity_label: description}`` and ``relations`` as relation names, and
returns a tuple ``(entities, relations)``. Each relation carries ``head``
and ``tail`` span dicts keyed ``type``, the relation name under
``relation``, and a ``score``. ``relex_backend._predict`` bridges this
into the backend's own triplet shape (mapping-like and attribute-like
objects stay accepted). If the installed gliner deviates, the raised
exception propagates to ``extract_with_fallback``, which records it
honestly and falls back. No silent success is possible.
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast, runtime_checkable


@runtime_checkable
class RelexSpan(Protocol):
    """One head or tail mention span inside a relation triplet."""

    text: str
    start: int
    end: int


@runtime_checkable
class RelexTriplet(Protocol):
    """One predicted relation triplet (attribute-style access)."""

    head: RelexSpan
    tail: RelexSpan
    label: str
    score: float


class RelexModel(Protocol):
    """Zero-shot joint NER + relation extraction model."""

    def predict_relations(
        self,
        text: str,
        labels: object,
        relations: list[str],
        flat_ner: bool = True,
        threshold: float = 0.5,
        adjacency_threshold: float | None = None,
        relation_threshold: float | None = None,
        multi_label: bool = False,
    ) -> tuple[list[object], list[object]]: ...


class GlinerFactory(Protocol):
    """``gliner.GLiNER`` class shape (classmethod entry point)."""

    def from_pretrained(self, model_id: str) -> RelexModel: ...


class GlinerModule(Protocol):
    """``gliner`` top-level module protocol."""

    GLiNER: GlinerFactory


def import_gliner() -> GlinerModule:
    """Lazily import ``gliner`` and cast to the typed Protocol."""
    return cast(GlinerModule, importlib.import_module("gliner"))

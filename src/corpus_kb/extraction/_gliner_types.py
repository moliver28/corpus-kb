"""Typed Protocol wrappers for the ``gliner`` package (GLiNER-Relex).

``gliner`` serves ONE role here: zero-shot relation extraction with the
GLiNER-Relex checkpoint (Stepanov et al. 2026). Entities come from
``gliner2`` (``_gliner2_types.py``). Imports go through
:func:`import_gliner` with the same lazy importlib string-import pattern,
so this module imports cleanly without torch and pyright checks against
the Protocols below, never the real package.

API assumption (pinned): ``gliner >= 0.2`` exposes
``GLiNER.from_pretrained(model_id)`` returning a model whose
``predict_relations(text, labels, entities=..., threshold=...)`` takes
``labels`` as ``(head_label, relation_label)`` pairs and returns a list
of relation triplets. Triplets carry head and tail spans, the relation
label, and a score. Triplet shapes are normalized defensively in
``relex_backend`` — mapping-like and attribute-like objects are both
accepted. If the installed gliner deviates, the raised exception
propagates to ``extract_with_fallback``, which records it honestly and
falls back. No silent success is possible.
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
        labels: list[tuple[str, str]],
        entities: list[str] | None = None,
        threshold: float = 0.5,
    ) -> list[object]: ...


class GlinerFactory(Protocol):
    """``gliner.GLiNER`` class shape (classmethod entry point)."""

    def from_pretrained(self, model_id: str) -> RelexModel: ...


class GlinerModule(Protocol):
    """``gliner`` top-level module protocol."""

    GLiNER: GlinerFactory


def import_gliner() -> GlinerModule:
    """Lazily import ``gliner`` and cast to the typed Protocol."""
    return cast(GlinerModule, importlib.import_module("gliner"))

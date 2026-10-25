"""Typed Protocol wrappers for the optional ``gliner2`` package.

``gliner2`` is an optional extra (``pip install .[ner]``) and is NEVER
installed in CI. Every import goes through :func:`import_gliner2` — the
same lazy importlib string-import pattern as ``_langextract_types.py`` —
so this module imports cleanly on a bare install and pyright type-checks
against the Protocols below, never the real package.

API assumption (pinned): ``gliner2 >= 2.1`` exposes
``GLiNER2.from_pretrained(model_id, revision=...)`` returning a model
whose ``extract_entities(text, schema)`` takes a ``dict[str, str]`` of
``{label: one-line description}`` and returns
``{label: [span, ...]}`` where each span carries ``text``, ``start``,
``end`` and ``score``. Spans are normalized defensively: mapping-like
and attribute-like span objects are both accepted. If the installed
gliner2 deviates, the raised exception propagates to
``extract_with_fallback``, which records it honestly and falls back —
no silent success is possible.
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast, runtime_checkable


@runtime_checkable
class GlinerSpan(Protocol):
    """One predicted mention span (attribute-style access)."""

    text: str
    start: int
    end: int
    score: float
    label: str


@runtime_checkable
class GlinerModel(Protocol):
    """Schema-conditioned entity extraction model."""

    def extract_entities(self, text: str, schema: dict[str, str]) -> dict[str, list[object]]: ...


@runtime_checkable
class GlinerBatchModel(Protocol):
    """Batched twin of ``extract_entities`` (gliner2 >= 2.1).

    Existence is probed at runtime (``isinstance``); the returned per-text
    payloads go through the same span normalization and an equivalence
    gate before the batch path is ever trusted (``gliner_predict.py``).
    """

    def batch_extract_entities(
        self, texts: list[str], schema: dict[str, str]
    ) -> list[dict[str, list[object]]]: ...


class Gliner2Factory(Protocol):
    """``gliner2.GLiNER2`` class shape (classmethod entry point)."""

    def from_pretrained(self, model_id: str, revision: str | None = None) -> GlinerModel: ...


class Gliner2Module(Protocol):
    """``gliner2`` top-level module protocol."""

    GLiNER2: Gliner2Factory


def import_gliner2() -> Gliner2Module:
    """Lazily import ``gliner2`` and cast to the typed Protocol.

    Raises:
        ImportError: If the ``gliner2`` extra is not installed.
    """
    return cast(Gliner2Module, importlib.import_module("gliner2"))

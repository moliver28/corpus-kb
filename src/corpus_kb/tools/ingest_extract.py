"""Extraction stage for the ingest pipeline (zero-drift Wave 3B split).

Moved byte-identical from ``ingest_common``: the extractor fallback chain
(``extract_with_fallback`` with its honest failure notes) and the config
reads (``_extractor_name``, ``_extract_entities_flag``).
``ingest_common`` re-exports the public names.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..extraction import Extractor, ExtractStats, create_extractor, extraction_backend_name
from ..extraction.pgml_backend import PgmlExtractor
from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from .ingest_cfg import _nested_dict

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_Extraction = tuple[list[Entity], list[Relation], ExtractStats]
_ExtractRun = Callable[[Extractor, list[Chunk], Ontology, str], Awaitable[_Extraction]]


@dataclass(frozen=True)
class _FallbackStep:
    """One rung of the extractor fallback chain in ``extract_with_fallback``.

    ``applies_to`` gates the rung by configured backend name. ``build``
    makes the extractor; ``run`` drives one extraction. ``accept_relations``
    lets the ml rung succeed on relations alone. ``reraise_value_error``
    propagates configuration errors for rungs where a silent downgrade is
    never allowed. The note and warning texts keep every fallback honest.
    """

    key: str
    applies_to: frozenset[str]
    build: Callable[[dict[str, object], asyncpg.Pool | None], Extractor]
    run: _ExtractRun
    accept_relations: bool
    reraise_value_error: bool
    empty_note: str
    empty_warning: str
    failure_warning: str


def _build_ml(config: dict[str, object], pool: asyncpg.Pool | None) -> Extractor:
    """The ml rung factory: profile seeds feed the customized mode."""
    return create_extractor(config, pool=pool, extra_examples=_customized_seeds(config))


def _build_configured(config: dict[str, object], pool: asyncpg.Pool | None) -> Extractor:
    """The plain factory for rungs that use the configured backend."""
    return create_extractor(config, pool=pool)


def _build_langextract(config: dict[str, object], pool: asyncpg.Pool | None) -> Extractor:
    """The langextract rung factory: force the langextract backend."""
    return create_extractor(
        {**config, "graph": {**_nested_dict(config, "graph"), "extractor": "langextract"}}
    )


async def _run_extract(
    extractor: Extractor, chunks: list[Chunk], ontology: Ontology, source_document_id: str
) -> _Extraction:
    """One sync extraction call, wrapped to fit the uniform rung loop."""
    return extractor.extract(chunks, ontology, source_document_id)


async def _run_pgml(
    extractor: Extractor, chunks: list[Chunk], ontology: Ontology, source_document_id: str
) -> _Extraction:
    """PostgresML extracts in the database when possible; others run sync."""
    if isinstance(extractor, PgmlExtractor):
        return await extractor.aextract(chunks, ontology, source_document_id)
    return extractor.extract(chunks, ontology, source_document_id)


# The fallback chain, in order. The langextract rung serves every backend
# that allows a langextract retry; the regex floor after the loop is fixed.
_FALLBACK_STEPS: tuple[_FallbackStep, ...] = (
    _FallbackStep(
        key="ml",
        applies_to=frozenset({"ml"}),
        build=_build_ml,
        run=_run_extract,
        accept_relations=True,
        reraise_value_error=True,
        empty_note="ml returned no candidates",
        empty_warning="ML extraction returned no candidates; falling back to langextract.",
        failure_warning="ML extraction failed: %s; falling back to langextract.",
    ),
    _FallbackStep(
        key="gliner",
        applies_to=frozenset({"gliner"}),
        build=_build_configured,
        run=_run_extract,
        accept_relations=False,
        reraise_value_error=True,
        empty_note="gliner returned no entities",
        empty_warning="Gliner extraction returned no entities; falling back to regex.",
        failure_warning="Gliner extraction failed: %s; falling back to regex.",
    ),
    _FallbackStep(
        key="pgml",
        applies_to=frozenset({"pgml"}),
        build=_build_configured,
        run=_run_pgml,
        accept_relations=False,
        reraise_value_error=False,
        empty_note="pgml returned no entities",
        empty_warning="PostgresML extraction returned no entities; falling back to langextract.",
        failure_warning="PostgresML extraction failed: %s; falling back to langextract.",
    ),
    _FallbackStep(
        key="langextract",
        applies_to=frozenset({"pgml", "langextract", "ml"}),
        build=_build_langextract,
        run=_run_extract,
        accept_relations=False,
        reraise_value_error=False,
        empty_note="langextract returned no entities",
        empty_warning="LangExtract extraction returned no entities; falling back to regex.",
        failure_warning="LangExtract extraction failed: %s; falling back to regex.",
    ),
)


def _customized_seeds(config: dict[str, object]) -> list[dict[str, object]] | None:
    """Profile seed exemplars for ``customized`` mode (None when none).

    The active profile was already validated by the pipeline entry, so a
    ValidationError here means a direct call with a broken profile — it
    propagates loudly, never a silent fallback.
    """
    from ..profiles import load_profile, load_seeds

    profile = load_profile(dict(config))
    if profile is None or profile.seeds_file is None:
        return None
    return load_seeds(dict(config), profile)


def _extractor_name(config: dict[str, object]) -> str:
    """The effective extraction backend (extraction.backend > graph.extractor)."""
    return extraction_backend_name(config)


async def extract_with_fallback(
    chunks: list[Chunk],
    ontology: Ontology,
    source_document_id: str,
    config: dict[str, object],
    pool: asyncpg.Pool | None = None,
) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
    """Extract entities and relations, falling back through the extractor chain.

    Order depends on the extraction backend (``extraction.backend``
    preferred, ``graph.extractor`` the accepted alias):

    - ``pgml``: PostgresML NER -> LangExtract -> RegexExtractor
    - ``langextract``: LangExtract -> RegexExtractor
    - ``gliner``: GLiNER2 NER -> RegexExtractor
    - ``ml``: GLiNER2 + GLiNER-Relex (mode-gated) -> LangExtract -> RegexExtractor
    - ``regex``: RegexExtractor only

    The returned ``extractor_id`` always names the extractor that actually
    produced the output. Every failure and every empty result along the
    chain is recorded in ``stats.notes`` — a fallback is never silent.

    Returns:
        (entities, relations, extractor_id, stats).
    """
    extractor_name = _extractor_name(config)
    failures: list[str] = []

    # Reject unknown extractor names explicitly so we never silently fall back
    # to regex when the user configured something unsupported.
    if extractor_name not in {"pgml", "langextract", "regex", "gliner", "ml"}:
        create_extractor(config, pool=pool)

    for step in _FALLBACK_STEPS:
        if extractor_name not in step.applies_to:
            continue
        try:
            extractor = step.build(config, pool)
            entities, relations, stats = await step.run(
                extractor, chunks, ontology, source_document_id
            )
            if entities or (step.accept_relations and relations):
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, extractor.extractor_id),
                    stats,
                )
            failures.append(step.empty_note)
            logger.warning(step.empty_warning)
        except Exception as exc:
            if step.reraise_value_error and isinstance(exc, ValueError):
                raise
            failures.append(f"{step.key}: {type(exc).__name__}: {exc}")
            logger.warning(step.failure_warning, exc)

    from ..extraction import RegexExtractor

    fallback = RegexExtractor()
    entities, relations, stats = fallback.extract(chunks, ontology, source_document_id)
    if failures:
        stats = replace(stats, notes=stats.notes + tuple(failures))
    return entities, relations, _actual_extractor_id(entities, fallback.extractor_id), stats


def _actual_extractor_id(entities: list[Entity], fallback_id: str) -> str:
    """Return the extractor_id that actually produced the entities.

    Extractors that internally fall back to RegexExtractor may leave
    entities with a different ``extractor_id`` than the wrapper's own id.
    When every entity agrees on a different id, report that id honestly.
    """
    if not entities:
        return fallback_id
    ids = {entity.extractor_id for entity in entities if entity.extractor_id}
    if len(ids) == 1:
        return ids.pop()
    return fallback_id


def _extract_entities_flag(config: dict[str, object]) -> bool:
    graph = _nested_dict(config, "graph")
    flag = graph.get("extract_entities")
    return flag if isinstance(flag, bool) else True

"""Extraction stage for the ingest pipeline (zero-drift Wave 3B split).

Moved byte-identical from ``ingest_common``: the extractor fallback chain
(``extract_with_fallback`` with its honest failure notes) and the config
reads (``_extractor_name``, ``_extract_entities_flag``).
``ingest_common`` re-exports the public names.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from ..extraction import ExtractStats, create_extractor, extraction_backend_name
from ..extraction.pgml_backend import PgmlExtractor
from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from .ingest_cfg import _nested_dict

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


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

    if extractor_name == "ml":
        try:
            ml_extractor = create_extractor(config, pool=pool)
            entities, relations, stats = ml_extractor.extract(chunks, ontology, source_document_id)
            if entities or relations:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, ml_extractor.extractor_id),
                    stats,
                )
            failures.append("ml returned no candidates")
            logger.warning("ML extraction returned no candidates; falling back to langextract.")
        except ValueError:
            # Configuration error (unknown mode, bad threshold): propagate
            # loudly instead of silently downgrading the user's explicit
            # extractor choice to regex.
            raise
        except Exception as exc:
            failures.append(f"ml: {type(exc).__name__}: {exc}")
            logger.warning("ML extraction failed: %s; falling back to langextract.", exc)

    if extractor_name == "gliner":
        try:
            gliner_extractor = create_extractor(config, pool=pool)
            entities, relations, stats = gliner_extractor.extract(
                chunks, ontology, source_document_id
            )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, gliner_extractor.extractor_id),
                    stats,
                )
            failures.append("gliner returned no entities")
            logger.warning("Gliner extraction returned no entities; falling back to regex.")
        except ValueError:
            # Configuration error (e.g. gliner not enabled, unknown mention
            # type): propagate loudly instead of silently downgrading the
            # user's explicit extractor choice to regex.
            raise
        except Exception as exc:
            failures.append(f"gliner: {type(exc).__name__}: {exc}")
            logger.warning("Gliner extraction failed: %s; falling back to regex.", exc)

    if extractor_name == "pgml":
        try:
            pgml_extractor = create_extractor(config, pool=pool)
            if isinstance(pgml_extractor, PgmlExtractor):
                entities, relations, stats = await pgml_extractor.aextract(
                    chunks, ontology, source_document_id
                )
            else:
                entities, relations, stats = pgml_extractor.extract(
                    chunks, ontology, source_document_id
                )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, pgml_extractor.extractor_id),
                    stats,
                )
            failures.append("pgml returned no entities")
            logger.warning(
                "PostgresML extraction returned no entities; falling back to langextract."
            )
        except Exception as exc:
            failures.append(f"pgml: {type(exc).__name__}: {exc}")
            logger.warning("PostgresML extraction failed: %s; falling back to langextract.", exc)

    if extractor_name in ("pgml", "langextract", "ml"):
        try:
            lang_extractor = create_extractor(
                {
                    **config,
                    "graph": {
                        **_nested_dict(config, "graph"),
                        "extractor": "langextract",
                    },
                }
            )
            entities, relations, stats = lang_extractor.extract(
                chunks, ontology, source_document_id
            )
            if entities:
                return (
                    entities,
                    relations,
                    _actual_extractor_id(entities, lang_extractor.extractor_id),
                    stats,
                )
            failures.append("langextract returned no entities")
            logger.warning("LangExtract extraction returned no entities; falling back to regex.")
        except Exception as exc:
            failures.append(f"langextract: {type(exc).__name__}: {exc}")
            logger.warning("LangExtract extraction failed: %s; falling back to regex.", exc)

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

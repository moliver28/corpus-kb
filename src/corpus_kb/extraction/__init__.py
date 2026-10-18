"""Pluggable ontology extractor seam."""

from __future__ import annotations

from typing import Any, cast

import asyncpg

from .gliner_backend import GlinerExtractor
from .langextract_backend import LangExtractExtractor
from .pgml_backend import PgmlExtractor
from .protocol import Extractor, ExtractStats, OntologyViolationError
from .regex_backend import RegexExtractor

# The extractor names create_extractor (and the ingest fallback chain in
# tools/ingest_extract.py) resolve. "llamaindex" is named but deferred.
EXTRACTION_BACKENDS: frozenset[str] = frozenset({"regex", "langextract", "pgml", "gliner"})


def extraction_backend_name(config: dict[str, Any]) -> str:
    """Resolve the extraction backend: ``extraction.backend`` wins.

    ``extraction.backend`` is the PREFERRED key (the old placement at
    ``graph.extractor`` mixed two concerns); the alias stays accepted
    forever. When both are set the preferred key wins; when only the alias
    is set it stays authoritative. Anything unreadable falls back to
    ``"pgml"`` (the shipped default). ``config.load_config`` mirrors the
    preferred key onto the alias so every reader agrees.
    """
    extraction = config.get("extraction")
    if isinstance(extraction, dict):
        raw = cast(dict[str, Any], extraction).get("backend")
        if isinstance(raw, str) and raw:
            return raw
    graph = config.get("graph")
    if isinstance(graph, dict):
        raw = cast(dict[str, Any], graph).get("extractor")
        if isinstance(raw, str) and raw:
            return raw
    return "pgml"


def create_extractor(config: dict[str, Any], pool: asyncpg.Pool | None = None) -> Extractor:
    """Create an extractor from the configuration dictionary.

    Args:
        config: Application configuration. Reads ``extraction.backend``
            (preferred) with ``graph.extractor`` as the accepted legacy
            alias, defaulting to ``"pgml"``. The ``"gliner"`` backend
            additionally reads ``extraction.gliner`` (``model``,
            ``revision``, ``types``) and requires
            ``extraction.gliner.enabled: true``.
        pool: Optional asyncpg pool for database-backed extractors
            (currently ``pgml``).

    Returns:
        Configured extractor instance.

    Raises:
        NotImplementedError: For the deferred ``"llamaindex"`` backend.
        ValueError: For an unknown extractor name, or ``"gliner"`` while
            ``extraction.gliner.enabled`` is false.
    """
    extractor_name = extraction_backend_name(config)
    fixture_dir: str | None = None
    live_fallback = False
    graph_config = config.get("graph", {})
    if isinstance(graph_config, dict):
        typed_config = cast(dict[str, Any], graph_config)
        raw_fixture_dir = typed_config.get("fixture_dir")
        if isinstance(raw_fixture_dir, str):
            fixture_dir = raw_fixture_dir
        raw_live_fallback = typed_config.get("live_fallback")
        if isinstance(raw_live_fallback, bool):
            live_fallback = raw_live_fallback

    match extractor_name:
        case "regex":
            return RegexExtractor()
        case "langextract":
            return LangExtractExtractor(fixture_dir=fixture_dir, live_fallback=live_fallback)
        case "pgml":
            return PgmlExtractor(pool=pool)
        case "gliner":
            return _create_gliner(config)
        case "llamaindex":
            raise NotImplementedError("llamaindex extractor is deferred to a later phase")
        case _ as unreachable:
            raise ValueError(f"Unsupported graph extractor: {unreachable}")


def _create_gliner(config: dict[str, Any]) -> GlinerExtractor:
    """Build the gliner extractor from ``extraction.gliner`` config.

    Opt-in: refuses to construct while ``extraction.gliner.enabled`` is
    not explicitly true, so a stray ``graph.extractor: gliner`` never
    silently loads a multi-hundred-MB model.
    """
    extraction_config = config.get("extraction", {})
    gliner_config: dict[str, Any] = {}
    if isinstance(extraction_config, dict):
        raw_gliner = cast(dict[str, Any], extraction_config).get("gliner", {})
        if isinstance(raw_gliner, dict):
            gliner_config = cast(dict[str, Any], raw_gliner)

    if gliner_config.get("enabled") is not True:
        raise ValueError("gliner extractor is opt-in: set extraction.gliner.enabled: true")

    model = gliner_config.get("model", "fastino/gliner2-large-v1")
    revision = gliner_config.get("revision", "main")
    types_raw = gliner_config.get("types")
    types: list[str] | None = (
        [t for t in types_raw if isinstance(t, str)] if isinstance(types_raw, list) else None
    )
    return GlinerExtractor(
        model=model if isinstance(model, str) else "fastino/gliner2-large-v1",
        revision=revision if isinstance(revision, str) else "main",
        types=types,
    )


__all__ = [
    "EXTRACTION_BACKENDS",
    "ExtractStats",
    "Extractor",
    "GlinerExtractor",
    "LangExtractExtractor",
    "OntologyViolationError",
    "PgmlExtractor",
    "RegexExtractor",
    "create_extractor",
    "extraction_backend_name",
]

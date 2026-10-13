"""Pluggable ontology extractor seam."""

from __future__ import annotations

from typing import cast

from .gliner_backend import GlinerExtractor
from .langextract_backend import LangExtractExtractor
from .pgml_backend import PgmlExtractor
from .protocol import Extractor, ExtractStats, OntologyViolationError
from .regex_backend import RegexExtractor


def create_extractor(config: dict[str, object], pool: object | None = None) -> Extractor:
    """Create an extractor from the configuration dictionary.

    Args:
        config: Application configuration. Reads ``graph.extractor``,
            defaulting to ``"pgml"``. The ``"gliner"`` backend additionally
            reads ``extraction.gliner`` (``model``, ``revision``, ``types``)
            and requires ``extraction.gliner.enabled: true``.
        pool: Optional asyncpg pool for database-backed extractors
            (currently ``pgml``).

    Returns:
        Configured extractor instance.

    Raises:
        NotImplementedError: For the deferred ``"llamaindex"`` backend.
        ValueError: For an unknown extractor name, or ``"gliner"`` while
            ``extraction.gliner.enabled`` is false.
    """
    graph_config = config.get("graph", {})
    extractor_name = "pgml"
    fixture_dir: str | None = None
    live_fallback = False
    if isinstance(graph_config, dict):
        typed_config = cast(dict[str, object], graph_config)
        raw_name = typed_config.get("extractor", "pgml")
        if isinstance(raw_name, str):
            extractor_name = raw_name
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


def _create_gliner(config: dict[str, object]) -> GlinerExtractor:
    """Build the gliner extractor from ``extraction.gliner`` config.

    Opt-in: refuses to construct while ``extraction.gliner.enabled`` is
    not explicitly true, so a stray ``graph.extractor: gliner`` never
    silently loads a multi-hundred-MB model.
    """
    extraction_config = config.get("extraction", {})
    gliner_config: dict[str, object] = {}
    if isinstance(extraction_config, dict):
        raw_gliner = cast(dict[str, object], extraction_config).get("gliner", {})
        if isinstance(raw_gliner, dict):
            gliner_config = cast(dict[str, object], raw_gliner)

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
    "ExtractStats",
    "Extractor",
    "GlinerExtractor",
    "LangExtractExtractor",
    "OntologyViolationError",
    "PgmlExtractor",
    "RegexExtractor",
    "create_extractor",
]

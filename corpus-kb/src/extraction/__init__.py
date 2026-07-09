"""Pluggable ontology extractor seam."""

from __future__ import annotations

from typing import cast

from ..extraction.langextract_backend import LangExtractExtractor
from ..extraction.protocol import Extractor, OntologyViolationError
from ..extraction.regex_backend import RegexExtractor


def create_extractor(config: dict[str, object]) -> Extractor:
    """Create an extractor from the configuration dictionary.

    Args:
        config: Application configuration. Reads ``graph.extractor``,
            defaulting to ``"langextract"``.

    Returns:
        Configured extractor instance.

    Raises:
        NotImplementedError: For the deferred ``"llamaindex"`` backend.
        ValueError: For an unknown extractor name.
    """
    graph_config = config.get("graph", {})
    extractor_name = "langextract"
    fixture_dir: str | None = None
    live_fallback = False
    if isinstance(graph_config, dict):
        typed_config = cast(dict[str, object], graph_config)
        raw_name = typed_config.get("extractor", "langextract")
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
            return LangExtractExtractor(
                fixture_dir=fixture_dir, live_fallback=live_fallback
            )
        case "llamaindex":
            raise NotImplementedError(
                "llamaindex extractor is deferred to a later phase"
            )
        case _ as unreachable:
            raise ValueError(f"Unsupported graph extractor: {unreachable}")


__all__ = [
    "Extractor",
    "LangExtractExtractor",
    "OntologyViolationError",
    "RegexExtractor",
    "create_extractor",
]

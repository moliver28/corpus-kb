"""Mode-gated composite ML extractor (``extraction.backend: ml``).

One extractor, three modes — the paved path for zero-net-new-code
extraction (zero-drift program rule 1):

- ``zero_shot`` (default): GLiNER2 entities + GLiNER-Relex relations.
  Both models are ontology-conditioned at inference; no examples, no
  fine-tuning, no regex anywhere.
- ``few_shot``: LangExtract with self-prompted examples derived from the
  ontology (the ``_langextract_types.build_examples`` ladder).
- ``customized``: few-shot plus project exemplars mined from confirmed
  review decisions (``icl.mine_exemplars``) supplied by the caller as
  ``extra_examples``. With no exemplars it degrades to ``few_shot`` and
  records the degradation in ``stats.notes`` — never silently.

Stats from member extractors are merged additively; every member note is
kept. Entity/relation ``extractor_id`` values stay attributed to the
member that produced them.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation
from .gliner_backend import GlinerExtractor
from .langextract_backend import LangExtractExtractor
from .protocol import Extractor, ExtractStats
from .relex_backend import DEFAULT_RELEX_MODEL, RelexExtractor

EXTRACTION_MODES: frozenset[str] = frozenset({"zero_shot", "few_shot", "customized"})
DEFAULT_GLINER2_MODEL = "fastino/gliner2-large-v1"


def extraction_mode(config: dict[str, Any]) -> str:
    """Read ``extraction.mode`` (default ``zero_shot``; unknown values fail)."""
    extraction = config.get("extraction")
    mode = "zero_shot"
    if isinstance(extraction, dict):
        raw = extraction.get("mode")
        if isinstance(raw, str) and raw:
            mode = raw
    if mode not in EXTRACTION_MODES:
        raise ValueError(f"Unknown extraction mode: {mode}; supported: {sorted(EXTRACTION_MODES)}")
    return mode


class MLExtractor:
    """Composite extractor behind ``extraction.backend: ml``."""

    extractor_id: str = "ml"

    def __init__(
        self,
        config: dict[str, Any],
        extra_examples: list[dict[str, object]] | None = None,
    ) -> None:
        self._mode = extraction_mode(config)
        self._degraded = False
        self._members: list[Extractor] = []
        if self._mode == "zero_shot":
            self._members = [
                _gliner_member(config),
                _relex_member(config),
            ]
        else:
            if self._mode == "customized" and not extra_examples:
                self._degraded = True
            self._members = [_langextract_member(config, extra_examples)]

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        if self._mode == "zero_shot":
            gliner, relex = self._members[0], self._members[1]
            entities, _, stats = gliner.extract(chunks, ontology, source_document_id)
            emitted, relations, relex_stats = relex.extract(
                chunks, ontology, source_document_id, entities=entities
            )
            merged = replace(_merge_stats(stats, relex_stats), backend=self.extractor_id)
            return entities + emitted, relations, merged
        merged = ExtractStats(backend=self.extractor_id, notes=())
        if self._degraded:
            merged = _with_notes(merged, "customized mode had no exemplars; ran few_shot")
        try:
            member_entities, member_relations, stats = self._members[0].extract(
                chunks, ontology, source_document_id
            )
        except ImportError:
            # A missing dependency is a typed-error condition, not an
            # empty success: re-raise so the fallback chain records the
            # real failure instead of "no candidates".
            raise
        except Exception as exc:
            merged = _with_notes(
                merged,
                f"{self._members[0].extractor_id} member failed: {type(exc).__name__}: {exc}",
            )
            return [], [], merged
        merged = _merge_stats(merged, stats)
        return member_entities, member_relations, merged


def _gliner_member(config: dict[str, Any]) -> GlinerExtractor:
    block = config.get("extraction")
    gliner = block.get("gliner", {}) if isinstance(block, dict) else {}
    typed = gliner if isinstance(gliner, dict) else {}
    model = typed.get("model")
    revision = typed.get("revision")
    types = typed.get("types")
    return GlinerExtractor(
        model=model if isinstance(model, str) else DEFAULT_GLINER2_MODEL,
        revision=revision if isinstance(revision, str) else "main",
        types=types if isinstance(types, list) else None,
    )


def _relex_member(config: dict[str, Any]) -> RelexExtractor:
    block = config.get("extraction")
    relex = block.get("relex", {}) if isinstance(block, dict) else {}
    typed = relex if isinstance(relex, dict) else {}
    gliner_block = block.get("gliner", {}) if isinstance(block, dict) else {}
    gliner_typed = gliner_block if isinstance(gliner_block, dict) else {}
    raw_types = gliner_typed.get("types")
    types = raw_types if isinstance(raw_types, list) else None
    model = typed.get("model")
    threshold = typed.get("threshold")
    max_pairs = typed.get("max_label_pairs")
    return RelexExtractor(
        model=model if isinstance(model, str) else DEFAULT_RELEX_MODEL,
        types=types,
        threshold=threshold if isinstance(threshold, (int, float)) else 0.5,
        max_label_pairs=max_pairs if isinstance(max_pairs, int) else 40,
    )


def _langextract_member(
    config: dict[str, Any],
    extra_examples: list[dict[str, object]] | None,
) -> LangExtractExtractor:
    graph = config.get("graph")
    typed_graph = graph if isinstance(graph, dict) else {}
    fixture_dir = typed_graph.get("fixture_dir")
    live_fallback = typed_graph.get("live_fallback")
    return LangExtractExtractor(
        fixture_dir=fixture_dir if isinstance(fixture_dir, str) else None,
        live_fallback=live_fallback if isinstance(live_fallback, bool) else False,
        extra_examples=extra_examples,
    )


def _merge_stats(acc: ExtractStats, stats: ExtractStats) -> ExtractStats:
    return ExtractStats(
        backend=acc.backend,
        requested_entities=acc.requested_entities + stats.requested_entities,
        kept_entities=acc.kept_entities + stats.kept_entities,
        requested_relations=acc.requested_relations + stats.requested_relations,
        kept_relations=acc.kept_relations + stats.kept_relations,
        dropped_by_cap=acc.dropped_by_cap + stats.dropped_by_cap,
        dropped_by_confidence=acc.dropped_by_confidence + stats.dropped_by_confidence,
        dropped_by_other=acc.dropped_by_other + stats.dropped_by_other,
        duration_ms=acc.duration_ms + stats.duration_ms,
        notes=acc.notes + stats.notes,
    )


def _with_notes(stats: ExtractStats, *notes: str) -> ExtractStats:
    return replace(stats, notes=stats.notes + tuple(notes))

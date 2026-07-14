"""PostgresML-backed NER extractor.

Calls ``pgml.transform()`` for in-database NER via ONNX models.
Falls back to RegexExtractor on any failure (pgml not installed, connection
error, model not found, etc.).
"""

from __future__ import annotations

import logging
from typing import Optional

from src.extraction.protocol import Extractor
from src.ontology import Ontology
from src.utils.models import Chunk, Entity, Relation

logger = logging.getLogger(__name__)


class PgmlExtractor:
    """NER extractor using PostgresML's ONNX runtime.

    Falls back to RegexExtractor when pgml is unavailable.
    """

    extractor_id: str = "pgml"

    def __init__(self, pool: Optional[object] = None) -> None:
        self._pool = pool
        self._fallback: Optional[Extractor] = None

    def _get_fallback(self) -> Extractor:
        if self._fallback is None:
            from src.extraction.regex_backend import RegexExtractor

            self._fallback = RegexExtractor()
        return self._fallback

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract entities via PostgresML NER.

        If pgml is not available or any error occurs, falls back to RegexExtractor.
        """
        if self._pool is None:
            logger.info("PgmlExtractor has no pool; falling back to RegexExtractor.")
            return self._get_fallback().extract(chunks, ontology, source_document_id)

        try:
            import asyncio

            return asyncio.get_event_loop().run_until_complete(
                self._extract_async(chunks, ontology, source_document_id)
            )
        except Exception as exc:
            logger.warning(
                "PostgresML NER failed: %s; falling back to RegexExtractor.", exc
            )
            return self._get_fallback().extract(chunks, ontology, source_document_id)

    async def _extract_async(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation]]:
        """Async extraction via pgml.transform()."""
        import asyncpg

        entities: list[Entity] = []
        seen_names: set[str] = set()

        pool: asyncpg.Pool = self._pool  # type: ignore[assignment]
        async with pool.acquire() as conn:
            for chunk in chunks:
                if not chunk.text or not chunk.text.strip():
                    continue
                # Call pgml.transform for NER
                rows = await conn.fetch(
                    "SELECT pgml.transform($1, $2) AS result",
                    "ner",
                    chunk.text[:10000],
                )
                for row in rows:
                    result = row["result"]
                    if isinstance(result, list):
                        for ent in result:
                            name = ent.get("entity") or ent.get("word", "")
                            label = ent.get("entity_group") or ent.get("label", "MISC")
                            if not name or name in seen_names:
                                continue
                            entity_type = _map_label(label, ontology)
                            entities.append(
                                Entity(
                                    name=name,
                                    entity_type=entity_type,
                                    source_type=chunk.source_type,
                                    source_document_id=source_document_id,
                                    chunk_id=chunk.chunk_id,
                                    extractor_id=self.extractor_id,
                                    metadata={"pgml_label": label},
                                )
                            )
                            seen_names.add(name)

        logger.info(
            "PostgresML NER: %d entities from %d chunks", len(entities), len(chunks)
        )
        return entities, []


def _map_label(label: str, ontology: Ontology) -> str:
    """Map a NER label to an ontology entity type."""
    label_upper = label.upper()
    mapping = {
        "PER": "Person",
        "PERSON": "Person",
        "ORG": "Org",
        "ORGANIZATION": "Org",
        "GPE": "Place",
        "LOC": "Place",
        "PRODUCT": "Product",
        "EVENT": "Concept",
        "WORK_OF_ART": "Concept",
        "MISC": "Concept",
    }
    mapped = mapping.get(label_upper, "Concept")
    if mapped in ontology.entity_types:
        return mapped
    if "Concept" in ontology.entity_types:
        return "Concept"
    if ontology.entity_types:
        return ontology.entity_types[0]
    return "Concept"

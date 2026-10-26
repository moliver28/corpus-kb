"""Honest extraction accounting: ExtractStats through the langextract
cap/confidence paths, failure notes through extract_with_fallback, and
write-boundary provenance/honest counts in store_entities/store_relations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from corpus_kb.ontology import load_ontology
from corpus_kb.tools.ingest_common import PostgresIngestStore, extract_with_fallback
from corpus_kb.utils.models import Chunk, Entity, Relation

_ONTOLOGY_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "config" / "ontology.yaml"
)


def _write_fixture(fixture_dir: Path, text: str, lines: list[dict[str, object]]) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    payload = "\n".join(json.dumps(line) for line in lines) + "\n"
    (fixture_dir / f"{key}.jsonl").write_text(payload, encoding="utf-8")


def _entity_line(name: str, start: int, end: int) -> dict[str, object]:
    return {
        "extraction_class": "Concept",
        "extraction_text": name,
        "char_interval": {"start_pos": start, "end_pos": end},
    }


def _relation_line(subject: str, object_: str) -> dict[str, object]:
    return {
        "extraction_class": "DEPENDS_ON",
        "extraction_text": "depends on",
        "attributes": {"subject": subject, "object": object_, "confidence": 0.9},
    }


async def test_langextract_cap_and_unresolved_drops_are_counted(tmp_path: Path) -> None:
    """12 resolvable relations + 1 ghost: 10 kept, 2 by cap, 1 other."""
    text = "Alpha depends on Beta."
    lines: list[dict[str, object]] = [
        _entity_line("Alpha", 0, 5),
        _entity_line("Beta", 17, 21),
    ]
    lines += [_relation_line("Alpha", "Beta") for _ in range(12)]
    lines.append(_relation_line("Ghost", "Beta"))
    _write_fixture(tmp_path, text, lines)

    from corpus_kb.extraction.langextract_backend import LangExtractExtractor

    extractor = LangExtractExtractor(fixture_dir=str(tmp_path), live_fallback=False)
    chunk = Chunk(chunk_id="c1", document_id="d1", text=text, source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)

    entities, relations, stats = extractor.extract([chunk], ontology, "d1")

    assert len(entities) == 2
    assert len(relations) == 10
    assert stats.requested_relations == 13
    assert stats.kept_relations == 10
    assert stats.dropped_by_cap == 2
    assert stats.dropped_by_other == 1
    assert stats.kept_entities == stats.requested_entities == 2


async def test_langextract_invalid_entity_offset_counts_as_dropped(tmp_path: Path) -> None:
    text = "Alpha depends on Beta."
    _write_fixture(
        tmp_path,
        text,
        [_entity_line("Alpha", 0, 999)],
    )

    from corpus_kb.extraction.langextract_backend import LangExtractExtractor

    extractor = LangExtractExtractor(fixture_dir=str(tmp_path), live_fallback=False)
    chunk = Chunk(chunk_id="c1", document_id="d1", text=text, source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)

    entities, _relations, stats = extractor.extract([chunk], ontology, "d1")

    assert entities == []
    assert stats.requested_entities == 1
    assert stats.kept_entities == 0
    assert stats.dropped_by_other == 1


async def test_extract_with_fallback_records_langextract_failure_and_falls_to_regex(
    tmp_path: Path,
) -> None:
    """Missing fixture + no live fallback: falls to regex, note recorded."""
    empty_dir = tmp_path / "no-fixtures"
    empty_dir.mkdir()
    config: dict[str, object] = {
        "graph": {
            "extractor": "langextract",
            "fixture_dir": str(empty_dir),
            "live_fallback": False,
        }
    }
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme ships widgets.", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)

    _entities, _relations, extractor_id, stats = await extract_with_fallback(
        [chunk], ontology, "d1", config
    )

    assert extractor_id == "regex"
    assert stats.backend == "regex"
    assert any(note.startswith("langextract: FileNotFoundError") for note in stats.notes)


async def test_extract_with_fallback_gliner_config_error_propagates() -> None:
    """Disabled gliner is a config error — never a silent regex downgrade."""
    chunk = Chunk(chunk_id="c1", document_id="d1", text="Acme", source_type="text")
    ontology = load_ontology(_ONTOLOGY_PATH)
    config: dict[str, object] = {"graph": {"extractor": "gliner"}}

    with pytest.raises(ValueError, match="gliner extractor is opt-in"):
        await extract_with_fallback([chunk], ontology, "d1", config)


# ---------------------------------------------------------------------------
# Write-boundary provenance + honest insert counts
# ---------------------------------------------------------------------------


class _StoreTx:
    """No-op transaction context: the fake has no real transaction to open."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _StoreConn:
    """Fake asyncpg connection: fetch returns scripted batch results."""

    def __init__(self, results: list[list[dict[str, str]]]) -> None:
        self._results = results
        self.calls: list[tuple[object, ...]] = []

    async def execute(self, sql: str, *args: object) -> str:
        return ""

    async def fetchrow(self, sql: str, *args: object) -> dict[str, str] | None:
        return None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.calls.append(args)
        index = len(self.calls) - 1
        scripted = self._results[index] if index < len(self._results) else []
        if "INSERT INTO entities" in sql:
            names = args[2]
            types = args[3]
            return [
                {"entity_id": row["entity_id"], "name": names[i], "entity_type": types[i]}
                for i, row in enumerate(scripted)
            ]
        return list(scripted)

    def transaction(self) -> _StoreTx:
        return _StoreTx()


class _StorePool:
    def __init__(self, conn: _StoreConn) -> None:
        self._conn = conn

    def acquire(self) -> _StorePool:
        return self

    async def __aenter__(self) -> _StoreConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


def _entity(
    name: str,
    confidence: float | None = 0.75,
    extractor_id: str = "gliner",
    metadata: dict[str, object] | None = None,
) -> Entity:
    return Entity(
        name=name,
        entity_type="Concept",
        source_type="text",
        source_document_id="d1",
        confidence=confidence,
        extractor_id=extractor_id,
        metadata=metadata if metadata is not None else {},
    )


async def test_store_entities_merges_provenance_and_counts_only_inserts() -> None:
    # The upsert RETURNS the canonical id on conflict too: both entities map.
    conn = _StoreConn([[{"entity_id": "e1"}, {"entity_id": "e2"}]])
    store = PostgresIngestStore(_StorePool(conn), "tenant")

    alpha, beta = _entity("Alpha"), _entity("Beta")
    id_map = await store.store_entities([alpha, beta])

    assert id_map == {alpha.entity_id: "e1", beta.entity_id: "e2"}
    assert len(conn.calls) == 1  # one batched statement, not one per entity
    persisted = json.loads(str(conn.calls[0][5][0]))
    assert persisted["extractor"] == "gliner"
    assert persisted["confidence"] == 0.75


async def test_store_entities_preserves_existing_metadata_keys() -> None:
    conn = _StoreConn([[{"entity_id": "e1"}]])
    store = PostgresIngestStore(_StorePool(conn), "tenant")
    entity = _entity("Alpha", metadata={"model": "fastino/gliner2-large-v1"})

    await store.store_entities([entity])

    persisted = json.loads(str(conn.calls[0][5][0]))
    assert persisted["model"] == "fastino/gliner2-large-v1"


async def test_store_relations_counts_only_inserts() -> None:
    conn = _StoreConn([[{"relation_id": "r1"}]])
    store = PostgresIngestStore(_StorePool(conn), "tenant")
    entity_a = _entity("Alpha")
    entity_b = _entity("Beta")
    relations = [
        Relation(
            source_entity_id=entity_a.entity_id,
            target_entity_id=entity_b.entity_id,
            relation_type="DEPENDS_ON",
        ),
        Relation(
            source_entity_id=entity_b.entity_id,
            target_entity_id=entity_a.entity_id,
            relation_type="DEPENDS_ON",
        ),
    ]

    count = await store.store_relations(relations)

    assert count == 1
    assert len(conn.calls) == 1  # one batched statement; conflicts land server-side

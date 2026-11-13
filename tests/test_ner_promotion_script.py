"""Unit tests for scripts/run_ner_promotion.py pure helpers.

The script itself needs the optional gliner models, so only the config
shaping and entity-to-span conversion run here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from corpus_kb.utils.models import Entity

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_ner_promotion.py"
_spec = importlib.util.spec_from_file_location("run_ner_promotion_test", _SCRIPT)
assert _spec is not None and _spec.loader is not None
prom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prom)


def _entity(
    entity_type: str = "Person",
    metadata: dict[str, object] | None = None,
    start: int | None = 0,
    end: int | None = 5,
) -> Entity:
    return Entity(
        name="Acme",
        entity_type=entity_type,
        source_type="text",
        source_document_id="d1",
        chunk_id="c1",
        source_start_char=start,
        source_end_char=end,
        confidence=0.9,
        extractor_id="gliner",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# _both_configs
# ---------------------------------------------------------------------------


def test_both_configs_candidate_gliner_enabled_baseline_ml() -> None:
    config: dict[str, Any] = {
        "extraction": {
            "backend": "ml",
            "mode": "zero_shot",
            "gliner": {
                "enabled": False,
                "model": "fastino/gliner2-large-v1",
                "revision": "main",
            },
        }
    }
    candidate, baseline = prom._both_configs(config)
    cand_block = candidate["extraction"]
    assert isinstance(cand_block, dict)
    assert cand_block["backend"] == "gliner"
    assert cand_block["gliner"]["enabled"] is True
    assert cand_block["gliner"]["model"] == "fastino/gliner2-large-v1"
    base_block = baseline["extraction"]
    assert isinstance(base_block, dict)
    assert base_block["backend"] == "ml"
    # The caller's config dict stays untouched.
    assert config["extraction"]["backend"] == "ml"  # type: ignore[typeddict-item]
    assert config["extraction"]["gliner"]["enabled"] is False


def test_both_configs_tolerate_missing_blocks() -> None:
    candidate, baseline = prom._both_configs({})
    cand_block = candidate["extraction"]
    base_block = baseline["extraction"]
    assert isinstance(cand_block, dict) and isinstance(base_block, dict)
    assert cand_block["backend"] == "gliner"
    assert base_block["backend"] == "ml"


# ---------------------------------------------------------------------------
# _entity_span
# ---------------------------------------------------------------------------


def test_entity_span_prefers_gliner_type_metadata() -> None:
    span = prom._entity_span(_entity(entity_type="Concept", metadata={"gliner_type": "date"}), 100)
    assert span is not None
    assert (span.start, span.end, span.label) == (0, 5, "date")


def test_entity_span_falls_back_to_inverse_mention_map() -> None:
    span = prom._entity_span(_entity(entity_type="Org", metadata={}), 100)
    assert span is not None
    assert span.label == "organization"


def test_entity_span_drops_unmappable_and_unscorable() -> None:
    assert prom._entity_span(_entity(entity_type="Concept"), 100) is None
    assert prom._entity_span(_entity(entity_type="Person", start=None), 100) is None
    assert prom._entity_span(_entity(entity_type="Person", end=999), 100) is None


# ---------------------------------------------------------------------------
# _INVERSE_MENTION
# ---------------------------------------------------------------------------


def test_inverse_mention_map_first_synonym_wins() -> None:
    assert prom._INVERSE_MENTION["Person"] == "person"
    assert prom._INVERSE_MENTION["Org"] == "organization"
    assert prom._INVERSE_MENTION["Money"] == "money"
    assert prom._INVERSE_MENTION["Product"] == "product"

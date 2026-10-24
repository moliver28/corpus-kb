"""extraction_tune handler + tune-data builder tests (Living Corpus W5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.extraction.lora_tune import MIN_TRAIN_EXAMPLES, tune_settings
from corpus_kb.extraction.tune_data import (
    statements_to_exemplars,
    to_gliner_example,
    tokenize_with_offsets,
)
from corpus_kb.tools.extraction_tune_tools import extraction_tune


class _FakeConn:
    """Answers the two mine queries; nothing else is touched."""

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "sc.resolved_verdict = 'revision'" in sql:
            return [
                {
                    "statement_id": "00000000-0000-0000-0000-00000000000a",
                    "statement_text": "I like ice cream.",
                    "entities": json.dumps([{"name": "ice cream", "entity_type": "CONCEPT"}]),
                }
            ]
        return []

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _FakeConn:
        return _FakeConn()


async def test_exemplar_arm_writes_seeds_and_profile(tmp_path: Path) -> None:
    config = {"profiles": {"dir": str(tmp_path)}}
    result = await extraction_tune(
        _FakePool(),
        config,
        "00000000-0000-0000-0000-000000000001",
        method="exemplar",
        profile="interviews",
        dry_run=False,
    )
    assert result["status"] == "success"
    assert result["method"] == "exemplar"
    assert result["exemplars_written"] == 1
    seeds = yaml.safe_load(Path(str(result["seeds_path"])).read_text(encoding="utf-8"))
    assert seeds["exemplars"][0]["text"] == "I like ice cream."
    profile = yaml.safe_load((tmp_path / "interviews.yaml").read_text(encoding="utf-8"))
    assert profile["seeds_file"] == "interviews.seeds.yaml"
    assert profile["name"] == "interviews"


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    config = {"profiles": {"dir": str(tmp_path)}}
    result = await extraction_tune(
        _FakePool(), config, "00000000-0000-0000-0000-000000000001", dry_run=True
    )
    assert result["dry_run"] is True
    assert result["exemplars_written"] == 0
    assert list(tmp_path.iterdir()) == []


async def test_unknown_method_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown tune method"):
        await extraction_tune(_FakePool(), {}, "t", method="magic")


async def test_bad_limit_and_confidence_rejected() -> None:
    with pytest.raises(ValidationError, match="limit"):
        await extraction_tune(_FakePool(), {}, "t", limit=0)
    with pytest.raises(ValidationError, match="min_confidence"):
        await extraction_tune(_FakePool(), {}, "t", min_confidence=7)


async def test_lora_missing_extra_is_typed(tmp_path: Path) -> None:
    """No tune extra installed -> DependencyMissingError (honest, not silent)."""
    config = {"profiles": {"dir": str(tmp_path)}}
    with pytest.raises((DependencyMissingError, ValidationError)):
        await extraction_tune(
            _FakePool(),
            config,
            "00000000-0000-0000-0000-000000000001",
            method="lora",
            dry_run=False,
        )


# -- tune_data builders


def test_tokenize_with_offsets_roundtrip() -> None:
    text = "I like ice cream."
    tokens, spans = tokenize_with_offsets(text)
    assert tokens == ["I", "like", "ice", "cream."]
    for token, (start, end) in zip(tokens, spans, strict=True):
        assert text[start:end] == token


def test_to_gliner_example_token_spans() -> None:
    row = to_gliner_example("I like ice cream a lot.", [("ice cream", "CONCEPT")])
    assert row is not None
    tokens = row["tokenized_text"]
    start, end, label = row["ner"][0]
    assert label == "CONCEPT"
    assert " ".join(tokens[start : end + 1]) == "ice cream"


def test_to_gliner_example_drops_unlocatable() -> None:
    assert to_gliner_example("Nothing here.", [("pistachio", "FOOD")]) is None


def test_statements_to_exemplars_shape() -> None:
    confirmed = [
        {
            "statement_id": "s1",
            "text": "I like ice cream.",
            "entities": [{"name": "ice cream", "entity_type": "CONCEPT"}],
        },
        {"statement_id": "s2", "text": "No entities.", "entities": []},
    ]
    exemplars = statements_to_exemplars(confirmed)
    assert len(exemplars) == 1
    assert exemplars[0]["extractions"][0]["extraction_class"] == "CONCEPT"


def test_tune_settings_defaults_and_overrides() -> None:
    assert tune_settings({})["steps"] == 200
    merged = tune_settings({"extraction": {"tune": {"steps": 42}}})
    assert merged["steps"] == 42
    assert merged["lora_r"] == 8


def test_min_train_examples_is_enforced_threshold() -> None:
    assert MIN_TRAIN_EXAMPLES >= 4

"""Typed ``ask.*`` settings tests (issue #540): defaults, errors, loader."""

from __future__ import annotations

import pytest

from corpus_kb.ask.ask_settings import (
    ASK_CONFIG_DEFAULTS,
    DEFAULT_ASK_MAX_SOURCES,
    AskSettings,
    ask_settings_error,
    load_ask_settings,
)
from corpus_kb.config import get_default_config
from corpus_kb.errors import ValidationError


def test_defaults_are_positive_and_typed() -> None:
    settings = load_ask_settings({})
    assert settings == AskSettings(max_sources=8)
    assert DEFAULT_ASK_MAX_SOURCES == 8
    assert ASK_CONFIG_DEFAULTS == {"max_sources": 8}


def test_config_block_overrides_the_default() -> None:
    settings = load_ask_settings({"ask": {"max_sources": 3}})
    assert settings.max_sources == 3


@pytest.mark.parametrize("bad", [0, -1, True, "8", 2.5, None])
def test_invalid_values_carry_remediation_text(bad: object) -> None:
    error = ask_settings_error({"ask": {"max_sources": bad}})
    assert error is not None
    assert error.startswith("ask.max_sources: must be a positive int")
    with pytest.raises(ValidationError):
        load_ask_settings({"ask": {"max_sources": bad}})


def test_valid_values_pass_the_gate() -> None:
    assert ask_settings_error({"ask": {"max_sources": 12}}) is None
    assert ask_settings_error({}) is None


def test_default_config_carries_the_ask_block() -> None:
    assert get_default_config()["ask"] == {"max_sources": 8}

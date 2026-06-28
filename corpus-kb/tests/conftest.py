"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import importlib
import sys

import pytest


def pytest_configure(config: object) -> None:
    config.addinivalue_line(
        "markers",
        "requires_ollama: mark test as needing a running Ollama service",
    )
    config.addinivalue_line(
        "markers",
        "requires_hi_res: mark test as needing Unstructured hi_res (detectron2)",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip tests that require capabilities unavailable on the current host."""
    if "requires_hi_res" in item.keywords:
        if sys.platform == "win32":
            pytest.skip("hi_res requires detectron2, unavailable on native Windows")
        try:
            importlib.import_module("detectron2")
        except ImportError:
            pytest.skip("hi_res requires detectron2")

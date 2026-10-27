"""Unit tests for the shared JSON flag/count coercion (api._body).

The socket and HTTP adapters must agree on edge inputs (zero-drift rule 1):
absent keys answer the default, the string "false" never turns into a
truthy flag, and a zero limit passes through instead of becoming the
default.
"""

from __future__ import annotations

import pytest

from corpus_kb.api._body import opt_bool, opt_int, opt_str


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, True, True),
        (None, False, False),
        (True, False, True),
        (False, True, False),
        ("true", False, True),
        ("False", True, False),
        (" false ", False, False),
        ("1", False, True),
        ("0", True, False),
        ("yes", False, True),
        ("off", True, False),
        ("", True, False),
        ("junk", True, True),
        ("junk", False, False),
        (0, True, False),
        (1, False, True),
        (2, False, True),
    ],
)
def test_opt_bool(raw: object, default: bool, expected: bool) -> None:
    assert opt_bool(raw, default) is expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, 8, 8),
        (0, 8, 0),
        ("0", 8, 0),
        (5, 8, 5),
        ("5", 8, 5),
        (5.0, 8, 5),
        ("junk", 8, 8),
    ],
)
def test_opt_int(raw: object, default: int, expected: int) -> None:
    assert opt_int(raw, default) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("advisory", "advisory"),
        ("off", "off"),
        # Junk keeps the old str() behavior: the gate answers with a typed
        # error instead of a silent default.
        (3, "3"),
        (True, "True"),
    ],
)
def test_opt_str(raw: object, expected: str | None) -> None:
    assert opt_str(raw) == expected

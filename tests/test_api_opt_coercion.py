"""Unit tests for the shared JSON flag/count coercion (api._body).

The socket and HTTP adapters must agree on edge inputs (zero-drift rule 1):
absent keys answer the default, the string "false" never turns into a
truthy flag, and a zero limit passes through instead of becoming the
default.
"""

from __future__ import annotations

import pytest

from corpus_kb.api._body import opt_bool, opt_int, opt_str
from corpus_kb.api.socket_ops import _opt_int as _socket_opt_int


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


class TestSocketOptIntContract:
    """The socket-side count coercion keeps raise-on-junk.

    This is the one deliberate difference from the HTTP ``opt_int``: the
    socket arms for the cluster and sentiment ops pass an explicit junk
    value to the caller as an error. A silent default would read a
    different limit than the one the client sent. The absent key answers
    None, so the tool keeps its own default.
    """

    def test_absent_key_answers_none(self) -> None:
        assert _socket_opt_int({}, "limit") is None

    def test_valid_int_passes_through(self) -> None:
        assert _socket_opt_int({"limit": 5}, "limit") == 5

    @pytest.mark.parametrize("junk", ["abc", [1], {"size": 2}, object()])
    def test_junk_value_raises(self, junk: object) -> None:
        with pytest.raises((TypeError, ValueError)):
            _socket_opt_int({"limit": junk}, "limit")

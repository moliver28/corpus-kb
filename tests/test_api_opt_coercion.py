"""Unit tests for the shared JSON flag/count coercion (api._body).

The socket and HTTP adapters must agree on edge inputs (zero-drift rule 1):
absent keys answer the default, the string "false" never turns into a
truthy flag, and a zero limit passes through instead of becoming the
default. Issue #238 made the count coercion STRICT: one helper, one
failure class — junk raises the taxonomy ValidationError on every surface
(no silent default, no ValueError leakage).
"""

from __future__ import annotations

import pytest

from corpus_kb.api._body import opt_bool, opt_float, opt_int, opt_str
from corpus_kb.errors import ValidationError


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
        (None, None, None),
        (0, 8, 0),
        ("0", 8, 0),
        (5, 8, 5),
        ("5", 8, 5),
        (5.0, 8, 5),
        (9223372036854775807, 8, 9223372036854775807),
    ],
)
def test_opt_int(raw: object, default: int | None, expected: int | None) -> None:
    assert opt_int(raw, default, name="limit") == expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (-5, None, -5),
        (-5, 8, -5),
        (-9223372036854775808, None, -9223372036854775808),
    ],
)
def test_opt_int_allows_negative_for_seeds(raw: object, default: int | None, expected: int) -> None:
    assert opt_int(raw, default, name="seed", allow_negative=True) == expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, 0.8, 0.8),
        (None, None, None),
        (0.5, 0.8, 0.5),
        ("0.5", 0.8, 0.5),
        (1, 0.8, 1.0),
    ],
)
def test_opt_float(raw: object, default: float | None, expected: float | None) -> None:
    assert opt_float(raw, default, name="threshold") == expected


@pytest.mark.parametrize(
    ("raw", "default"),
    [
        ("junk", 8),
        ("junk", None),
        ("5.7", 8),
        (5.7, 8),
        (True, 8),
        ([1], 8),
        ({"size": 2}, 8),
        (object(), None),
        # Negative counts hit a Postgres negative-LIMIT rejection one layer
        # down (a 500/-32603); the parse helper refuses them up front.
        (-3, 8),
        (" -3 ", 8),
        # Past the Postgres int8 bind range the database answers -32603.
        (9223372036854775808, 8),
        (10**20, None),
    ],
)
def test_opt_int_junk_raises_validation_error(raw: object, default: int | None) -> None:
    """Junk and out-of-range counts raise the taxonomy error, never a silent default."""
    with pytest.raises(ValidationError):
        opt_int(raw, default, name="limit")


@pytest.mark.parametrize(
    ("raw", "default"),
    [
        ("junk", 0.8),
        (True, 0.8),
        ([1], None),
        ("nan", 0.8),
        ("inf", 0.8),
        ("1e999", 0.8),
        (float("nan"), 0.8),
    ],
)
def test_opt_float_junk_raises_validation_error(raw: object, default: float | None) -> None:
    """Junk and non-finite measures (NaN poisons comparisons) raise the taxonomy error."""
    with pytest.raises(ValidationError):
        opt_float(raw, default, name="threshold")


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


class TestStrictCountFailureClass:
    """One strict count helper serves every surface (issue #238).

    The old socket-only ``_opt_int`` twin is gone: the HTTP ``opt_int``
    now raises on an explicit junk value too, so a junk limit answers the
    taxonomy ValidationError everywhere — HTTP 400, socket -32602 — with
    no silent default and no ValueError leakage. The absent key still
    answers the default (which may be None, the tool's own default).
    """

    def test_absent_key_answers_none(self) -> None:
        assert opt_int(None, None, name="limit") is None

    def test_absent_key_answers_explicit_default(self) -> None:
        assert opt_int(None, 50, name="limit") == 50

    def test_zero_passes_through(self) -> None:
        assert opt_int(0, 8, name="limit") == 0

    def test_junk_message_names_the_param(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            opt_int("abc", 8, name="limit")
        assert "limit" in str(excinfo.value)

    def test_taxonomy_triplet(self) -> None:
        assert (ValidationError.http_status, ValidationError.socket_code) == (400, -32602)

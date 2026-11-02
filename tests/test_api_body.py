"""parse_body contract: a bad or non-dict JSON body answers an empty dict.

Error-injection pins for the api/_body.py parse swallow (issue 235): the
sentinel must come from the parse failure alone. A healthy dict body must
pass through untouched, so an empty dict is never a silent success.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from corpus_kb.api._body import parse_body


class _RaisingRequest:
    """Starlette Request stand-in whose json() always raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def json(self) -> Any:
        raise self._exc


class _StaticRequest:
    """Starlette Request stand-in that answers one canned body."""

    def __init__(self, body: Any) -> None:
        self._body = body

    async def json(self) -> Any:
        return self._body


async def test_healthy_dict_body_passes_through() -> None:
    body = {"model": "stub", "messages": []}
    assert await parse_body(cast("Any", _StaticRequest(body))) is body


@pytest.mark.parametrize("exc", [ValueError("bad json"), RuntimeError("connection reset")])
async def test_malformed_json_body_answers_empty_dict(exc: Exception) -> None:
    assert await parse_body(cast("Any", _RaisingRequest(exc))) == {}


@pytest.mark.parametrize("body", [[1, 2], "scalar", 7, None])
async def test_non_dict_json_body_answers_empty_dict(body: Any) -> None:
    assert await parse_body(cast("Any", _StaticRequest(body))) == {}

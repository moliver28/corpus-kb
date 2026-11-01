"""The parser registry: names, cache tags, and seams for ``textbook.parser``.

One module owns the whole parser surface, so adding a parser touches
one place plus its own parse module. ``PARSER_VERSIONS`` maps the
parser name to the version tag baked into cache records; a tag change
invalidates the records the old tag wrote. ``parser_key`` derives the
caller-side cache tag, so a flag flip caches the rich variant under
its own tag and never serves the other variant's records.

``ParserSeam`` and ``_PARSER_SEAMS`` are the dispatch rows: one row per
parser that needs an optional extra. ``pypdf`` has no row — it is the
degrade path every row falls back to, and its reader is already open
when windows parse.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .textbook_docling import PageItem
from .textbook_paddle import confirm_paddle, parse_one_paddle_window
from .textbook_settings import TextbookSettings
from .textbook_windows import confirm_docling, parse_one_docling_window

#: Version tags baked into cache records; a parser change invalidates the
#: records the old version wrote (the cache key carries this tag).
PARSER_VERSIONS: dict[str, str] = {
    "docling": "docling-v2",
    "paddle-vl": "paddle-vl-v1",
    "pypdf": "pypdf-v1",
}

#: Parsers whose ``structured_output`` flag changes the parse output, so
#: the flag-on variant caches under its own ``-rich`` tag. The paddle
#: route recognizes page text only (no layout detector), so its output
#: does not change with the flag and it stays out of this set.
PARSER_STRUCTURED_VARIANTS: frozenset[str] = frozenset({"docling"})


def parser_key(parser: str, structured: bool) -> str:
    """The cache tag for one parser and structured-output flag pair.

    The registry drives the rich variant: a parser in
    ``PARSER_STRUCTURED_VARIANTS`` caches its flag-on output under its
    own tag, so a flag flip never serves the other variant's records.
    An unknown parser falls back to the Docling tag (the caller owns
    the warning).
    """
    name = parser if parser in PARSER_VERSIONS else "docling"
    base = PARSER_VERSIONS[name]
    if structured and name in PARSER_STRUCTURED_VARIANTS:
        return f"{base}-rich"
    return base


@dataclass(frozen=True)
class ParserSeam:
    """One optional parser's two edges: the extra check and the window call.

    ``confirm`` runs once per book, on the first uncached window, and
    returns ``(active, state)``. ``parse_window`` runs one window
    through the loaded state and returns ``None`` to hand the window to
    the pypdf fallback.
    """

    confirm: Callable[[TextbookSettings, Any | None, list[str]], tuple[bool, Any | None]]
    parse_window: Callable[
        [TextbookSettings, Any | None, Path, int, int, int, list[str]],
        list[PageItem] | None,
    ]


#: The registry behind ``textbook.parser``.
_PARSER_SEAMS: dict[str, ParserSeam] = {
    "docling": ParserSeam(confirm_docling, parse_one_docling_window),
    "paddle-vl": ParserSeam(confirm_paddle, parse_one_paddle_window),
}

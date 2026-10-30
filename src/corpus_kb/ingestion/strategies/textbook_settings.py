"""The ``textbook:`` config block, read and defaulted in one place.

Keys (all optional; unknown shapes keep the default):

* ``min_pages`` — the page-count gate (default 120)
* ``window_pages`` — pages per parse window (default 50)
* ``parser`` — ``docling`` (default) or ``pypdf``
* ``cache_dir`` — parsed-window cache root (default
  ``~/.corpus-kb/textbook-cache``)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_PAGES = 120
DEFAULT_WINDOW_PAGES = 50
DEFAULT_PARSER = "docling"
DEFAULT_CACHE_DIR = "~/.corpus-kb/textbook-cache"


@dataclass(frozen=True)
class TextbookSettings:
    """The ``textbook:`` config block, defaulted field by field."""

    min_pages: int = DEFAULT_MIN_PAGES
    window_pages: int = DEFAULT_WINDOW_PAGES
    parser: str = DEFAULT_PARSER
    cache_dir: str = DEFAULT_CACHE_DIR


def _int_setting(raw: object, default: int) -> int:
    """A positive int setting, or the default (bools are not counts)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return default


def _str_setting(raw: object, default: str) -> str:
    """A non-empty string setting, or the default."""
    return raw if isinstance(raw, str) and raw else default


def settings_from(config: Mapping[str, object] | None) -> TextbookSettings:
    """Read the ``textbook:`` config block; unknown shapes keep defaults."""
    block = config.get("textbook") if config else None
    block = block if isinstance(block, Mapping) else {}
    return TextbookSettings(
        min_pages=_int_setting(block.get("min_pages"), DEFAULT_MIN_PAGES),
        window_pages=_int_setting(block.get("window_pages"), DEFAULT_WINDOW_PAGES),
        parser=_str_setting(block.get("parser"), DEFAULT_PARSER),
        cache_dir=_str_setting(block.get("cache_dir"), DEFAULT_CACHE_DIR),
    )

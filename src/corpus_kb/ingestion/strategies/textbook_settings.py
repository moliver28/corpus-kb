"""The ``textbook:`` config block, read and defaulted in one place.

Keys (all optional; unknown shapes keep the default):

* ``min_pages`` — the page-count gate (default 120)
* ``window_pages`` — pages per parse window (default 50)
* ``parser`` — ``docling`` (default) or ``pypdf``
* ``cache_dir`` — parsed-window cache root (default
  ``~/.corpus-kb/textbook-cache``)
* ``window_subprocess`` — parse each Docling window in a child process
  so a scanner crash cannot kill the worker (default true)
* ``window_timeout_s`` — seconds before a child window parse is killed
  and the window falls back to pypdf (default 3600.0)
* ``cache_max_mb`` — window-cache size cap in MiB; the oldest files
  evict first, and ``0`` disables the cap (default 2048)
* ``structured_output`` — keep rich page structure (heading level,
  page box, table markdown) in the parsed windows (default false)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_PAGES = 120
DEFAULT_WINDOW_PAGES = 50
DEFAULT_PARSER = "docling"
DEFAULT_CACHE_DIR = "~/.corpus-kb/textbook-cache"
DEFAULT_WINDOW_SUBPROCESS = True
DEFAULT_WINDOW_TIMEOUT_S = 3600.0
DEFAULT_CACHE_MAX_MB = 2048
DEFAULT_STRUCTURED_OUTPUT = False


@dataclass(frozen=True)
class TextbookSettings:
    """The ``textbook:`` config block, defaulted field by field."""

    min_pages: int = DEFAULT_MIN_PAGES
    window_pages: int = DEFAULT_WINDOW_PAGES
    parser: str = DEFAULT_PARSER
    cache_dir: str = DEFAULT_CACHE_DIR
    window_subprocess: bool = DEFAULT_WINDOW_SUBPROCESS
    window_timeout_s: float = DEFAULT_WINDOW_TIMEOUT_S
    cache_max_mb: int = DEFAULT_CACHE_MAX_MB
    structured_output: bool = DEFAULT_STRUCTURED_OUTPUT


def _int_setting(raw: object, default: int) -> int:
    """A positive int setting, or the default (bools are not counts)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return default


def _nonneg_int_setting(raw: object, default: int) -> int:
    """A non-negative int setting, or the default (0 is a real value)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return default


def _float_setting(raw: object, default: float) -> float:
    """A positive number setting, or the default (bools are not numbers)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return default


def _str_setting(raw: object, default: str) -> str:
    """A non-empty string setting, or the default."""
    return raw if isinstance(raw, str) and raw else default


def _bool_setting(raw: object, default: bool) -> bool:
    """A bool setting, or the default (ints are not flags here)."""
    return raw if isinstance(raw, bool) else default


def settings_from(config: Mapping[str, object] | None) -> TextbookSettings:
    """Read the ``textbook:`` config block; unknown shapes keep defaults."""
    block = config.get("textbook") if config else None
    block = block if isinstance(block, Mapping) else {}
    return TextbookSettings(
        min_pages=_int_setting(block.get("min_pages"), DEFAULT_MIN_PAGES),
        window_pages=_int_setting(block.get("window_pages"), DEFAULT_WINDOW_PAGES),
        parser=_str_setting(block.get("parser"), DEFAULT_PARSER),
        cache_dir=_str_setting(block.get("cache_dir"), DEFAULT_CACHE_DIR),
        window_subprocess=_bool_setting(block.get("window_subprocess"), DEFAULT_WINDOW_SUBPROCESS),
        window_timeout_s=_float_setting(block.get("window_timeout_s"), DEFAULT_WINDOW_TIMEOUT_S),
        cache_max_mb=_nonneg_int_setting(block.get("cache_max_mb"), DEFAULT_CACHE_MAX_MB),
        structured_output=_bool_setting(block.get("structured_output"), DEFAULT_STRUCTURED_OUTPUT),
    )

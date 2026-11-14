"""The ``textbook:`` config block, read and defaulted in one place.

Keys (all optional; unknown shapes keep the default):

* ``min_pages`` — the page-count gate (default 120)
* ``window_pages`` — pages per parse window (default 50)
* ``parser`` — ``docling`` (default), ``paddle-vl`` (the ``[ocr-vl]``
  challenger), or ``pypdf`` (the ``[scan]`` degrade parser)
* ``cache_dir`` — parsed-window cache root (default
  ``~/.corpus-kb/textbook-cache``)
* ``window_subprocess`` — parse each heavy window (docling, paddle-vl)
  in a child process so a scanner crash cannot kill the worker
  (default true)
* ``window_timeout_s`` — seconds before a child window parse is killed
  and the window falls back to pypdf (default 3600.0)
* ``worker_reuse`` — keep one child worker alive across windows and
  documents in subprocess mode, so the parser's model load is paid
  once per worker, not once per window (issue #270; default true)
* ``worker_max_windows`` — windows one child worker serves before the
  parent recycles it, so no worker grows without bound (default 32)
* ``worker_idle_ttl_s`` — seconds an idle worker child stays alive;
  the parent drops a worker whose last served window sits further back
  than this when the next window asks for it, so a long-lived server
  sheds idle parser children instead of holding them forever
  (default 600.0)
* ``cache_max_mb`` — window-cache size cap in MiB; the oldest files
  evict first, and ``0`` disables the cap (default 2048)
* ``structured_output`` — keep rich page structure (heading level,
  page box, table markdown) in the parsed windows (default false)
* ``citations`` — write a plain-English ``citation`` string (the true
  pages and the section title) into each chunk's metadata
  (default false)
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
DEFAULT_WORKER_REUSE = True
DEFAULT_WORKER_MAX_WINDOWS = 32
DEFAULT_WORKER_IDLE_TTL_S = 600.0
DEFAULT_CACHE_MAX_MB = 2048
DEFAULT_STRUCTURED_OUTPUT = False
DEFAULT_CITATIONS = False


@dataclass(frozen=True)
class WorkerPolicy:
    """The persistent-worker policy, read from the ``textbook:`` settings.

    ``reuse`` off restores spawn-per-window isolation exactly as issue
    #219 shipped it. ``max_windows`` recycles the worker after that
    many served windows, so one worker never grows without bound.
    ``idle_ttl_s`` drops a worker idle longer than that at the next
    acquire, so an idle server does not hold a loaded parser child
    for its whole life.
    """

    reuse: bool = DEFAULT_WORKER_REUSE
    max_windows: int = DEFAULT_WORKER_MAX_WINDOWS
    idle_ttl_s: float = DEFAULT_WORKER_IDLE_TTL_S


@dataclass(frozen=True)
class TextbookSettings:
    """The ``textbook:`` config block, defaulted field by field."""

    min_pages: int = DEFAULT_MIN_PAGES
    window_pages: int = DEFAULT_WINDOW_PAGES
    parser: str = DEFAULT_PARSER
    cache_dir: str = DEFAULT_CACHE_DIR
    window_subprocess: bool = DEFAULT_WINDOW_SUBPROCESS
    window_timeout_s: float = DEFAULT_WINDOW_TIMEOUT_S
    worker_reuse: bool = DEFAULT_WORKER_REUSE
    worker_max_windows: int = DEFAULT_WORKER_MAX_WINDOWS
    worker_idle_ttl_s: float = DEFAULT_WORKER_IDLE_TTL_S
    cache_max_mb: int = DEFAULT_CACHE_MAX_MB
    structured_output: bool = DEFAULT_STRUCTURED_OUTPUT
    citations: bool = DEFAULT_CITATIONS

    def worker_policy(self) -> WorkerPolicy:
        """The persistent-worker policy for this block."""
        return WorkerPolicy(
            reuse=self.worker_reuse,
            max_windows=self.worker_max_windows,
            idle_ttl_s=self.worker_idle_ttl_s,
        )


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
        worker_reuse=_bool_setting(block.get("worker_reuse"), DEFAULT_WORKER_REUSE),
        worker_max_windows=_int_setting(
            block.get("worker_max_windows"), DEFAULT_WORKER_MAX_WINDOWS
        ),
        worker_idle_ttl_s=_float_setting(block.get("worker_idle_ttl_s"), DEFAULT_WORKER_IDLE_TTL_S),
        cache_max_mb=_nonneg_int_setting(block.get("cache_max_mb"), DEFAULT_CACHE_MAX_MB),
        structured_output=_bool_setting(block.get("structured_output"), DEFAULT_STRUCTURED_OUTPUT),
        citations=_bool_setting(block.get("citations"), DEFAULT_CITATIONS),
    )

"""On-disk window cache for the textbook strategy.

Parsed windows cache as JSON files under ``textbook.cache_dir``, keyed by
(parser version, file sha256, window index): one :class:`CacheKey` owns
the file name AND the record-match rule, so the name and the stored
triple can never drift apart. A retry or resume loads its parsed windows
instead of paying the parse again; a changed file, a changed parser, a
record whose span differs from the requested window, or a corrupted
record all miss and force a re-parse. Pure stdlib: no parser imports,
fully deterministic round trip.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .textbook_pages import PARSER_VERSIONS, PageItem, WindowRecord

logger = logging.getLogger(__name__)

_BLOCK = 1 << 20

#: A file this fresh may be mid-save; pruning leaves it for a later run.
_FRESH_SECONDS = 60.0


@dataclass(frozen=True)
class CacheKey:
    """One cache entry's identity: parser, file bytes, and window index.

    The single owner of the entry's file name and of the record-match
    rule, so a saved record and a later load can never disagree about
    what the key parts are.
    """

    parser_key: str
    sha256: str
    window_index: int

    @property
    def parser_version(self) -> str:
        """The version tag this key's records carry (``PARSER_VERSIONS``)."""
        return PARSER_VERSIONS[self.parser_key]

    def path(self, cache_dir: Path) -> Path:
        """The JSON cache file for this triple."""
        return cache_dir / f"{self.parser_key}-{self.sha256[:16]}-w{self.window_index:04d}.json"

    def matches(self, parser: object, sha256: object, window: object) -> bool:
        """True when a record's stored key parts are this key's parts.

        The window check is a strict int check: JSON ``true`` must never
        equal the window index 1.
        """
        return (
            parser == self.parser_version
            and sha256 == self.sha256
            and type(window) is int
            and window == self.window_index
        )


def file_sha256(path: Path) -> str:
    """The hex sha256 of the file bytes (streamed, fixed 1 MiB blocks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def window_ranges(page_count: int, window_pages: int) -> list[tuple[int, int]]:
    """1-based inclusive (start, end) spans covering pages 1..page_count."""
    return [
        (start, min(start + window_pages - 1, page_count))
        for start in range(1, page_count + 1, window_pages)
    ]


def load_window_cache(
    path: Path, key: CacheKey, start_page: int, end_page: int
) -> WindowRecord | None:
    """The cached window when the record matches the key and the span.

    A missing file, unreadable JSON, or any key/schema mismatch returns
    None: the caller re-parses the window (cache misses are always safe).
    The match is EXACT: the record's span must equal the requested
    window, and every cached page must sit inside it — a stale subset
    span would silently leave the window's later pages unparsed, and a
    foreign record that names other pages would serve wrong text, so
    both miss too.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if not key.matches(raw.get("parser"), raw.get("sha256"), raw.get("window")):
        return None
    start = raw.get("start_page")
    end = raw.get("end_page")
    pages = raw.get("pages")
    if type(start) is not int or type(end) is not int or not isinstance(pages, list):
        return None
    if start != start_page or end != end_page:
        return None
    items: list[PageItem] = []
    for row in pages:
        if not isinstance(row, dict):
            return None
        page = row.get("page")
        label = row.get("label")
        text = row.get("text")
        if type(page) is not int or not isinstance(label, str) or not isinstance(text, str):
            return None
        if not start <= page <= end:
            return None
        items.append(PageItem(page, label, text))
    return WindowRecord(start_page=start, end_page=end, items=items)


def save_window_cache(path: Path, key: CacheKey, record: WindowRecord) -> None:
    """Write one window record atomically; a failed write logs, never raises.

    The bytes land in a temp file first and ``os.replace`` moves them
    into place, so a concurrent prune never unlinks a half-written file
    and a reader never sees torn JSON. The record's key parts come from
    ``key`` — the same owner the load matches against. Keys sort: the
    bytes are stable for a snapshot. OSError is the one tolerated
    failure.
    """
    payload = {
        "parser": key.parser_version,
        "sha256": key.sha256,
        "window": key.window_index,
        "start_page": record.start_page,
        "end_page": record.end_page,
        "pages": [
            {"page": item.page, "label": item.label, "text": item.text} for item in record.items
        ],
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        logger.warning("Textbook window cache write failed for %s: %s", path, exc)


def prune_window_cache(cache_dir: Path, max_mb: int) -> int:
    """Delete oldest files first until the cache fits ``max_mb``; count them.

    A window record runs 4-8 MB and the cache is content-addressed, so an
    unpruned directory grows without end (issue #221). ``max_mb`` of 0 or
    less disables the cap and deletes nothing. A file younger than 60
    seconds may be mid-save and stays. One unreadable or undeletable file
    is skipped; pruning never raises.
    """
    if max_mb <= 0:
        return 0
    cap_bytes = max_mb * _BLOCK
    now = time.time()
    stats: list[tuple[float, int, Path]] = []
    try:
        entries = list(cache_dir.glob("*.json"))
    except OSError:
        return 0
    for path in entries:
        try:
            info = path.stat()
            if now - info.st_mtime < _FRESH_SECONDS:
                continue  # an in-flight save: never pruned, never counted
            stats.append((info.st_mtime, info.st_size, path))
        except OSError:
            continue
    total = sum(size for _mtime, size, _path in stats)
    deleted = 0
    for _mtime, size, path in sorted(stats):
        if total <= cap_bytes:
            break
        try:
            path.unlink()
        except OSError:
            logger.warning("Textbook window cache prune could not delete %s", path)
            continue
        deleted += 1
        total -= size
    return deleted

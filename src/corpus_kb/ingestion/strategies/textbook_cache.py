"""On-disk window cache for the textbook strategy.

Parsed windows cache as JSON files under ``textbook.cache_dir``, keyed by
(parser version, file sha256, window index): the file name carries a
parser tag plus the short hash, and the record repeats all three key
parts. A retry or resume loads its parsed windows instead of paying the
parse again; a changed file, a changed parser, or a corrupted record all
miss and force a re-parse. Pure stdlib: no parser imports, fully
deterministic round trip.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .textbook_pages import PageItem, WindowRecord

logger = logging.getLogger(__name__)

_BLOCK = 1 << 20


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


def cache_file(cache_dir: Path, parser_key: str, sha256: str, window_index: int) -> Path:
    """The JSON cache path for one (parser, file, window) triple."""
    return cache_dir / f"{parser_key}-{sha256[:16]}-w{window_index:04d}.json"


def load_window_cache(
    path: Path, parser_version: str, sha256: str, window_index: int
) -> WindowRecord | None:
    """The cached window when the record matches all three key parts.

    A missing file, unreadable JSON, or any key/schema mismatch returns
    None: the caller re-parses the window (cache misses are always safe).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("parser") != parser_version or raw.get("sha256") != sha256:
        return None
    if raw.get("window") != window_index:
        return None
    start = raw.get("start_page")
    end = raw.get("end_page")
    pages = raw.get("pages")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(pages, list):
        return None
    items: list[PageItem] = []
    for row in pages:
        if not isinstance(row, dict):
            return None
        page = row.get("page")
        label = row.get("label")
        text = row.get("text")
        if not isinstance(page, int) or not isinstance(label, str) or not isinstance(text, str):
            return None
        items.append(PageItem(page, label, text))
    return WindowRecord(start_page=start, end_page=end, items=items)


def save_window_cache(
    path: Path, parser_version: str, sha256: str, window_index: int, record: WindowRecord
) -> None:
    """Write one window record; a failed write logs and never raises.

    A missing cache must not fail ingestion, so OSError is the one
    tolerated failure here. Keys sort: the bytes are stable for a snapshot.
    """
    payload = {
        "parser": parser_version,
        "sha256": sha256,
        "window": window_index,
        "start_page": record.start_page,
        "end_page": record.end_page,
        "pages": [
            {"page": item.page, "label": item.label, "text": item.text} for item in record.items
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning("Textbook window cache write failed for %s: %s", path, exc)

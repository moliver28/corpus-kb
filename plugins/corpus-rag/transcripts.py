"""Transcript detection and watch scans for the study hooks (issue #352).

A transcript is a ``.vtt`` or ``.srt`` file; or a name with "transcript"
and a ``.md``, ``.txt``, or ``.docx`` suffix; or a ``.md``, ``.txt``, or
``.docx`` whose first 4096 bytes hold 3 or more speaker labels with at
least one label repeated. The ``.docx`` head comes from
``word/document.xml`` (stdlib ``zipfile``).

The speaker-label shape mirrors the engine's ``SPEAKER_LINE`` (bold
labels included), so the hooks queue exactly what the engine can parse.

Exclude patterns are a gitwildmatch subset: ``*`` and ``?`` inside one
segment, ``**`` across segments, slash-less patterns match any basename.
A custom ``exclude`` list in ``.claude/corpus.json`` replaces the
defaults. Scans stop at the depth, file-count cap, or deadline.

Stdlib only.
"""

from __future__ import annotations

import os
import re
import time
import zipfile
from pathlib import Path

SUBTITLE_SUFFIXES = (".vtt", ".srt")
DOC_SUFFIXES = (".md", ".txt", ".docx")
TRANSCRIPT_NAME_MARK = "transcript"
HEAD_BYTES = 4096

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "interviews/participant-key.md",
    "**/raw-transcripts/**",
    "analysis/**",
    "deliverables/**",
    "**/*-summary.md",
    "**/*-key-points.md",
)

SCAN_DEPTH = 4
SCAN_CAP = 300
SCAN_DEADLINE_SECONDS = 2.0

_LABEL_PREFIX = re.compile(r"^\s*(?:\*\*)?([A-Z0-9][\w .'\-]{0,60}?)(?:\*\*)?\s*:")
_XML_TAG = re.compile(r"<[^>]+>")


def excluded(rel_path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """True when a study-folder-relative posix path matches one pattern."""
    norm = rel_path.replace("\\", "/").lstrip("/")
    return any(_glob_regex(pattern).match(norm) for pattern in patterns)


def _glob_regex(pattern: str) -> re.Pattern:
    raw = pattern.strip().lstrip("/")
    parts = raw.split("/")
    pieces: list[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            pieces.append(".*" if last else "(?:[^/]+/)*")
        else:
            pieces.append(_segment_regex(part) + ("" if last else "/"))
    body = "".join(pieces)
    if "/" not in raw:
        body = "(?:.*/)?" + _segment_regex(raw)
    return re.compile("^" + body + "$")


def _segment_regex(segment: str) -> str:
    out: list[str] = []
    for char in segment:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)


def is_transcript(path: Path) -> bool:
    """The transcript rule: subtitle suffix, name mark, or label-rich head."""
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in SUBTITLE_SUFFIXES:
        return True
    if suffix not in DOC_SUFFIXES:
        return False
    if TRANSCRIPT_NAME_MARK in name:
        return True
    head = head_text(path)
    labels, repeated = count_labels(head)
    return labels >= 3 and repeated


def head_text(path: Path) -> str:
    """The file head as text; a .docx head comes from word/document.xml."""
    try:
        if path.suffix.lower() == ".docx":
            return _docx_head(path)
        with path.open("rb") as handle:
            return handle.read(HEAD_BYTES).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""


def _docx_head(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("word/document.xml")[: HEAD_BYTES * 8]
    text = _XML_TAG.sub("", raw.decode("utf-8", errors="replace"))
    return text[:HEAD_BYTES]


def count_labels(head: str) -> tuple[int, bool]:
    """Labelled line count in the head, and whether one label repeats."""
    counts: dict[str, int] = {}
    for line in head.splitlines():
        match = _LABEL_PREFIX.match(line.strip())
        if match:
            label = match.group(1).strip().lower()
            counts[label] = counts.get(label, 0) + 1
    return sum(counts.values()), any(count >= 2 for count in counts.values())


def scan_files(
    root: Path,
    patterns: list[str] | tuple[str, ...],
    depth: int = SCAN_DEPTH,
    cap: int = SCAN_CAP,
    deadline: float | None = None,
) -> list[Path]:
    """Files under ``root`` (depth-bounded, capped, excluded paths out).

    The result sorts by path, so the same folder scans to the same list.
    Hidden files and folders, and the ``.claude`` state folder, never scan.
    An expired deadline stops the scan with what it has (possibly nothing).
    """
    stop = time.monotonic() + deadline if deadline is not None else None
    found: list[str] = []
    root = root.resolve()
    base_depth = len(root.parts)
    for current, dirs, names in os.walk(root):
        if stop is not None and time.monotonic() >= stop:
            break
        if len(Path(current).parts) - base_depth >= depth:
            dirs[:] = []
        rel = Path(current).relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if _keep_dir(root, current, d, rel, patterns))
        for name in sorted(names):
            if name.startswith("."):
                continue
            rel_file = f"{rel}/{name}" if rel and rel != "." else name
            if patterns and excluded(rel_file, patterns):
                continue
            found.append(str(root / rel_file))
            if len(found) >= cap:
                return [Path(item) for item in sorted(found)]
    return [Path(item) for item in sorted(found)]


def _keep_dir(
    root: Path, current: str, name: str, rel: str, patterns: list[str] | tuple[str, ...]
) -> bool:
    if name.startswith("."):
        return False
    if not patterns:
        return True
    rel_dir = f"{rel}/{name}" if rel and rel != "." else name
    return not excluded(f"{rel_dir}/", patterns)

"""The shared folder-state function for every study hook (issue #352).

One function, all hooks use it. States:

``corpus``    ``.claude/corpus.json`` names a slug, the registry lists it,
              ``enabled`` is not false, and the folder is not declined.
``research``  no corpus, but the folder has transcripts (signal scan).
``off``       ``enabled: false`` or declined.
``none``      anything else, and always the engine repo itself.

``.claude/corpus.json`` keys: ``slug`` (legacy ``study``), ``watch``
(folders, list), ``exclude`` (gitwildmatch globs; replaces the defaults),
``auto_ingest`` (default true), ``enabled``, ``uxr.ticket``.

Stdlib only.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import gate_state
import study_registry
import transcripts
from engine_paths import plugin_data_dir

CORPUS = "corpus"
RESEARCH = "research"
OFF = "off"
NONE = "none"

MARKER_DIR = ".claude"
MARKER_FILE = "corpus.json"
ENGINE_SOURCE_DIR = Path("src") / "corpus_kb"

RESEARCH_SCAN_DEPTH = 3
RESEARCH_SCAN_CAP = 200
RESEARCH_SCAN_DEADLINE = 2.0

DEFAULT_EXCLUDES = transcripts.DEFAULT_EXCLUDES


@dataclass
class FolderState:
    """One folder's verdict plus the corpus.json values the hooks need."""

    state: str
    root: Path
    data: dict = field(default_factory=dict)
    slug: str | None = None
    watch: list[Path] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    auto_ingest: bool = True

    def watch_dirs(self) -> list[Path]:
        """Watch folders that exist right now."""
        return [path for path in self.watch if path.is_dir()]


def marker_path(folder: Path) -> Path:
    return folder / MARKER_DIR / MARKER_FILE


def declined_mark_name(folder: Path) -> str:
    """One declined mark per folder, keyed by the folder path checksum."""
    key = zlib.crc32(str(folder.resolve()).encode("utf-8"))
    return f"declined-folder-{key}"


def is_declined(folder: Path) -> bool:
    """Declined via the mark file or the setup gate state (issue #353)."""
    marks = plugin_data_dir() / "marks"
    if (marks / declined_mark_name(folder)).is_file():
        return True
    return gate_state.is_declined(folder)


def read_corpus_json(start: Path) -> Path | None:
    """The nearest folder holding a readable ``.claude/corpus.json``."""
    current = start.resolve()
    while True:
        marker = marker_path(current)
        if marker.is_file():
            try:
                json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return current
        if current.parent == current:
            return None
        current = current.parent


def _marker_data(root: Path) -> dict:
    try:
        data = json.loads(marker_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _slug_of(data: dict) -> str | None:
    for key in ("slug", "study"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _watch_paths(root: Path, data: dict) -> list[Path]:
    raw = data.get("watch")
    rows = raw if isinstance(raw, list) else ["."]
    out = []
    for item in rows:
        if not isinstance(item, str) or not item.strip():
            continue
        out.append((root / item).resolve())
    return out or [root]


def folder_state(
    start: Path | None = None, registry: dict | None = None, scan: bool = True
) -> FolderState:
    """The one folder-state function every hook shares.

    ``scan=False`` skips the research signal scan (CLI paths use it: they
    only need the corpus verdict, never the setup offer).
    """
    begin = start if start is not None else Path.cwd()
    root = read_corpus_json(begin)
    if root is None:
        if scan and begin.is_dir():
            return _research_or_none(begin)
        return FolderState(NONE, begin)
    if (root / ENGINE_SOURCE_DIR).is_dir():
        return FolderState(NONE, root)
    data = _marker_data(root)
    if data.get("enabled") is False:
        return FolderState(OFF, root, data)
    if is_declined(root):
        return FolderState(OFF, root, data)
    slug = _slug_of(data)
    if slug is None:
        return FolderState(NONE, root, data)
    if registry is None:
        registry = study_registry.load_registry()
    if slug not in registry.get("studies", {}):
        return FolderState(NONE, root, data, slug=slug)
    exclude = data.get("exclude")
    patterns = exclude if isinstance(exclude, list) and exclude else list(
        transcripts.DEFAULT_EXCLUDES
    )
    auto = data.get("auto_ingest")
    return FolderState(
        CORPUS,
        root,
        data,
        slug=slug,
        watch=_watch_paths(root, data),
        exclude=[str(item) for item in patterns],
        auto_ingest=True if not isinstance(auto, bool) else auto,
    )


def _research_or_none(begin: Path) -> FolderState:
    """No corpus.json: research when the folder holds transcripts."""
    folder = begin.resolve()
    found = transcripts.scan_files(
        folder,
        list(transcripts.DEFAULT_EXCLUDES),
        depth=RESEARCH_SCAN_DEPTH,
        cap=RESEARCH_SCAN_CAP,
        deadline=RESEARCH_SCAN_DEADLINE,
    )
    for path in found:
        if transcripts.is_transcript(path):
            return FolderState(RESEARCH, folder)
    return FolderState(NONE, folder)

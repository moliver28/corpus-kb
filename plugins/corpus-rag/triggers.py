"""The prompt trigger list and matcher for the setup offer (issue #353).

Data file: ``hooks/triggers.json`` with three lists — ``nouns``,
``frames``, and ``exclusions``. A prompt matches when it holds at least
one noun, at least one frame, and no exclusion.

Term shapes:

- ``transcript*`` — ``*`` is a word-prefix wildcard: any word that starts
  with the stem counts.
- ``what did ... say`` — ``...`` allows up to 80 characters between the
  words around it.
- ``{"term": "code*", "not": ["codebook"]}`` — the term matches, but a
  word that starts with a ``not`` prefix never counts for it.

Matching is case-insensitive and word-based. Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TRIGGERS_FILENAME = "triggers.json"
ELLIPSIS = "..."
ELLIPSIS_GAP = 80
WORD_CHARS = r"[A-Za-z0-9'_-]*"


class TriggerError(Exception):
    """The trigger file is missing or malformed."""


def triggers_path(plugin_root: Path | None = None) -> Path:
    root = plugin_root if plugin_root is not None else Path(__file__).resolve().parent
    return root / "hooks" / TRIGGERS_FILENAME


def load_triggers(plugin_root: Path | None = None) -> dict:
    """The trigger lists; empty lists when the file is absent."""
    path = triggers_path(plugin_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"nouns": [], "frames": [], "exclusions": []}
    if not isinstance(data, dict):
        return {"nouns": [], "frames": [], "exclusions": []}
    out: dict[str, list] = {}
    for key in ("nouns", "frames", "exclusions"):
        rows = data.get(key)
        out[key] = rows if isinstance(rows, list) else []
    return out


def _term_text(entry: object) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("term"), str):
        return entry["term"]
    return ""


def _blocked_prefixes(entry: object) -> list[str]:
    if isinstance(entry, dict):
        rows = entry.get("not")
        if isinstance(rows, list):
            return [str(item).lower() for item in rows if isinstance(item, str)]
    return []


def _word_regex(term: str) -> re.Pattern | None:
    """One term to a word-anchored pattern. None when it cannot match."""
    lowered = term.lower().strip()
    if not lowered:
        return None
    if ELLIPSIS in lowered:
        left, right = lowered.split(ELLIPSIS, 1)
        pattern = rf"\b{re.escape(left.strip())}\b.{{0,{ELLIPSIS_GAP}}}?"
        if right.strip():
            pattern += r"\b" + re.escape(right.strip()) + r"\b"
        return re.compile(pattern)
    pieces = []
    for part in lowered.split():
        stem, wildcard, rest = part.partition("*")
        if rest:
            # a * inside the word: prefix, then the literal rest
            pieces.append(re.escape(stem) + WORD_CHARS + re.escape(rest))
        else:
            pieces.append(re.escape(stem) + (WORD_CHARS if wildcard else ""))
    pattern = r"\b" + r"[\s-]+".join(pieces) + (r"" if lowered.endswith("*") else r"\b")
    return re.compile(pattern)


def _find(term_entry: object, text: str) -> re.Match | None:
    """The match for one term entry, honoring its ``not`` prefixes."""
    term = _term_text(term_entry)
    pattern = _word_regex(term)
    if pattern is None:
        return None
    blocked = _blocked_prefixes(term_entry)
    for match in pattern.finditer(text):
        if not blocked:
            return match
        word = match.group(0).lower()
        if not any(word.startswith(prefix) for prefix in blocked):
            return match
    return None


def prompt_terms(prompt: str, plugin_root: Path | None = None) -> dict[str, list[str]]:
    """Which nouns, frames, and exclusions appear in the prompt."""
    triggers = load_triggers(plugin_root)
    text = prompt.lower()
    out: dict[str, list[str]] = {"nouns": [], "frames": [], "exclusions": []}
    for key in out:
        for entry in triggers[key]:
            if _find(entry, text) is not None:
                out[key].append(_term_text(entry))
    return out


def matches(prompt: str, plugin_root: Path | None = None) -> bool:
    """A noun, a frame, and no exclusion make the offer trigger fire."""
    terms = prompt_terms(prompt, plugin_root)
    if terms["exclusions"]:
        return False
    return bool(terms["nouns"]) and bool(terms["frames"])

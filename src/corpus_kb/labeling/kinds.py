"""The built-in labeling-function kinds and their scorers.

Four kinds ship (the ``lfs.py`` file schema):

- ``regex`` — fires when the pattern matches the chunk text.
  Case-sensitive; put inline flags in the pattern.
- ``lexicon`` — fires when any term matches on word boundaries.
- ``negation_regex`` — a match counts only when no negator (``not``,
  ``never``, ``no``, or a contraction like ``don't``) appears in the
  ``negation_window`` characters before it.
- ``metadata`` — fires when the chunk metadata carries ``key`` with
  exactly that value (web keys such as ``og:type`` live in
  ``chunks.metadata``).

Every scorer is pure and deterministic: config + text + metadata in,
fired/not-fired out. Patterns compile through one cached seam, so
repeated scoring does not re-parse regexes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from functools import lru_cache

__all__ = [
    "DEFAULT_NEGATION_WINDOW",
    "SUPPORTED_KINDS",
    "applier_for",
    "compile_pattern",
]

SUPPORTED_KINDS = frozenset({"regex", "lexicon", "negation_regex", "metadata"})
DEFAULT_NEGATION_WINDOW = 40

# Negators searched case-blind inside the window before a match.
_NEGATOR_RE = re.compile(r"\b(?:not|never|no)\b|[a-z]n't", re.IGNORECASE)

Applier = Callable[[Mapping[str, object], str, Mapping[str, object]], bool]


@lru_cache(maxsize=256)
def compile_pattern(pattern: str, flags: int) -> re.Pattern[str]:
    """Compile (and cache) one regex; invalid patterns raise here."""
    return re.compile(pattern, flags)


def applier_for(kind: str) -> Applier:
    """The built-in scorer for one kind; unknown kinds raise with a fix."""
    try:
        return _APPLIERS[kind]
    except KeyError:
        supported = ", ".join(sorted(SUPPORTED_KINDS))
        raise ValueError(
            f"Unknown labeling-function kind {kind!r}. Supported kinds: {supported}."
        ) from None


def _apply_regex(config: Mapping[str, object], text: str, metadata: Mapping[str, object]) -> bool:
    del metadata
    return compile_pattern(str(config["pattern"]), 0).search(text) is not None


def _apply_lexicon(config: Mapping[str, object], text: str, metadata: Mapping[str, object]) -> bool:
    del metadata
    raw_terms = config.get("terms", ())
    terms: list[str] = []
    if isinstance(raw_terms, (list, tuple)):
        terms = [str(term) for term in raw_terms]
    flags = 0 if config.get("case_sensitive") else re.IGNORECASE
    alternation = "|".join(re.escape(term) for term in terms)
    pattern = compile_pattern(f"(?<!\\w)(?:{alternation})(?!\\w)", flags)
    return pattern.search(text) is not None


def _apply_negation_regex(
    config: Mapping[str, object], text: str, metadata: Mapping[str, object]
) -> bool:
    del metadata
    raw_window = config.get("negation_window", DEFAULT_NEGATION_WINDOW)
    window = int(raw_window) if isinstance(raw_window, int) else DEFAULT_NEGATION_WINDOW
    for match in compile_pattern(str(config["pattern"]), 0).finditer(text):
        start = max(0, match.start() - window)
        if _NEGATOR_RE.search(text[start : match.start()]) is None:
            return True
    return False


def _apply_metadata(
    config: Mapping[str, object], text: str, metadata: Mapping[str, object]
) -> bool:
    del text
    value = metadata.get(str(config["key"]))
    return isinstance(value, str) and value == str(config["equals"])


_APPLIERS: dict[str, Applier] = {
    "regex": _apply_regex,
    "lexicon": _apply_lexicon,
    "negation_regex": _apply_negation_regex,
    "metadata": _apply_metadata,
}

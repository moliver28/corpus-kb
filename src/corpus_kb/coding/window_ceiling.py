"""Coder window hard token ceiling (contract item 5).

Pure stdlib text transforms: deterministic, no I/O, no model call. The
token estimate is the repo's shared ~4-chars-per-token rule from
``chunking.hierarchy`` — a documented estimate, not a model-true count.

Policy (from the numbered contract in ``coder_window.py``): the window is
truncated when it exceeds the ceiling, and the anchor — the output
contract plus the worked example — is never cut. The chained context
yields before the target chunk, because the evidence quote must come
from the chunk. Every cut carries an explicit ``[truncated]`` marker,
so the model never sees a silently clipped text.

Budget exactness: ``build_window`` measures its fixed frame with EMPTY
slots for chunk and context. Because ``ceil`` is subadditive, swapping
the empty slots for fitted text can only add ``estimate(content)`` per
slot, so ``fixed + budget == ceiling`` bounds the rendered window.
"""

from __future__ import annotations

from corpus_kb.chunking.hierarchy import CHARS_PER_TOKEN, estimate_tokens

# Local coder models commonly run 8k-token context windows; 6k leaves
# room for the reply.
DEFAULT_MAX_WINDOW_TOKENS = 6_000

_TRUNCATION_MARKER = "\n... [truncated]"


def fit_to_tokens(text: str, budget: int) -> str:
    """Cut ``text`` to about ``budget`` estimated tokens at a word boundary.

    Returns the text unchanged when it already fits, and "" when the
    budget is too small to hold even the truncation marker.
    """
    if budget <= 0 or estimate_tokens(text) <= budget:
        return text if budget > 0 else ""
    max_chars = budget * CHARS_PER_TOKEN - len(_TRUNCATION_MARKER)
    if max_chars <= 0:
        return ""
    cut = text[:max_chars]
    boundary = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + _TRUNCATION_MARKER


def reserve_budget(ceiling: int, fixed_tokens: int) -> int:
    """Reserve the fixed window sections inside the ceiling.

    Raises ValueError when the ceiling cannot hold them (or is below 1);
    otherwise returns the token budget left for chunk and context.
    """
    if ceiling < 1 or ceiling <= fixed_tokens:
        raise ValueError(
            f"Coder window ceiling {ceiling} cannot hold the fixed window sections "
            f"(about {fixed_tokens} tokens). Raise max_tokens."
        )
    return ceiling - fixed_tokens

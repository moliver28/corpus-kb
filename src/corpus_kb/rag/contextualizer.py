"""Anthropic-style Contextual Retrieval blurb generator.

Generates a short LLM blurb situating a chunk within its parent document,
mirroring corpus_kb/extraction/langextract_backend.py's fixture-keyed,
live_fallback-gated, deterministic-at-temperature-0 pattern. Never raises
into the ingest pipeline: any failure degrades to an empty blurb, which
`coalesce()` treats as blurb-free at both embed and FTS-index time.

W3 contextualize v2 (settings in rag/contextual_settings.py):

- ``prompt_profile`` selects the template family. ``interview`` is EXPLICIT
  for transcript sources (names the moderator question); ``generic`` is a
  neutral template for any document.
- ``document_first_prompt`` orders the two context blocks: True = document
  first (the default; the Anthropic-style recommendation), False =
  chunk-first. Profiles x orderings = 4 template combinations.
- ``max_context_tokens`` caps the GENERATED blurb (chars/4 estimate, the
  chunking/hierarchy.py convention); the blurb truncates at the last
  sentence or word boundary within the cap. ``_MAX_DOC_CHARS`` stays the
  separate internal cap on the document context fed INTO the prompt.
- The fixture cache key includes the profile, so recorded fixtures never
  collide across profiles.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ollama import Client

from .contextual_settings import ContextualSettings, load_contextual_settings

logger = logging.getLogger(__name__)

# ~4 chars per token, matching chunking/hierarchy.py estimate_tokens.
_CHARS_PER_TOKEN = 4

_MAX_DOC_CHARS = 8000  # bound cost of the prompt sent to the LLM

# Bound on every generate call (seconds), matching
# nli_client.NLI_TIMEOUT_SECONDS / rag.judge.JUDGE_TIMEOUT_SECONDS: the
# ollama client otherwise waits forever on a wedged server (accepts TCP,
# never answers). Slow-beyond-120s generations DEGRADE to an empty blurb
# via the except below — never hang the ingest pipeline.
CONTEXTUAL_TIMEOUT_SECONDS = 120.0

_INTERVIEW_DOCUMENT_FIRST = (
    "Here is the document:\n<document>\n{document}\n</document>\n\n"
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "The document is a research or interview transcript. Identify the "
    "moderator or participant question this chunk answers, then give a "
    "short succinct context to situate the chunk within the overall "
    "document for the purposes of improving search retrieval of the chunk. "
    "Answer only with the succinct context and nothing else."
)
_INTERVIEW_CHUNK_FIRST = (
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Here is the document:\n<document>\n{document}\n</document>\n\n"
    "The document is a research or interview transcript. Identify the "
    "moderator or participant question this chunk answers, then give a "
    "short succinct context to situate the chunk within the overall "
    "document for the purposes of improving search retrieval of the chunk. "
    "Answer only with the succinct context and nothing else."
)
_GENERIC_DOCUMENT_FIRST = (
    "Here is the document:\n<document>\n{document}\n</document>\n\n"
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Please give a short succinct context to situate this chunk within the "
    "overall document for the purposes of improving search retrieval of "
    "the chunk. Answer only with the succinct context and nothing else."
)
_GENERIC_CHUNK_FIRST = (
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Here is the document:\n<document>\n{document}\n</document>\n\n"
    "Please give a short succinct context to situate this chunk within the "
    "overall document for the purposes of improving search retrieval of "
    "the chunk. Answer only with the succinct context and nothing else."
)

# (prompt_profile, document_first) -> template. All four combinations are
# unit-tested (tests/test_contextualizer.py template matrix).
PROMPT_TEMPLATES: dict[tuple[str, bool], str] = {
    ("interview", True): _INTERVIEW_DOCUMENT_FIRST,
    ("interview", False): _INTERVIEW_CHUNK_FIRST,
    ("generic", True): _GENERIC_DOCUMENT_FIRST,
    ("generic", False): _GENERIC_CHUNK_FIRST,
}


class ContextGenerator:
    def __init__(self, config: dict[str, object] | None = None) -> None:
        self.settings: ContextualSettings = load_contextual_settings(config or {})
        fixture_dir = self.settings.fixture_dir
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self._client = Client(host=self.settings.base_url, timeout=CONTEXTUAL_TIMEOUT_SECONDS)

    def generate_blurb(self, document_text: str, chunk_text: str) -> str:
        key = _fixture_key(
            document_text[:_MAX_DOC_CHARS],
            chunk_text,
            self.settings.prompt_profile,
        )
        if self.fixture_dir is not None:
            fixture_path = self.fixture_dir / f"{key}.txt"
            if fixture_path.exists():
                return fixture_path.read_text(encoding="utf-8").strip()
            if not self.settings.live_fallback:
                return ""
        try:
            return _cap_blurb(
                self._client_generate(document_text[:_MAX_DOC_CHARS], chunk_text),
                self.settings.max_context_tokens,
            )
        except Exception as exc:  # never raise into ingest; covers every
            # httpx.TransportError (timeout, RemoteProtocolError, ...) too
            logger.warning("Contextualizer unavailable: %s; chunk stays blurb-free.", exc)
            return ""

    def _client_generate(self, document_text: str, chunk_text: str) -> str:
        template = PROMPT_TEMPLATES[
            (self.settings.prompt_profile, self.settings.document_first_prompt)
        ]
        prompt = template.format(document=document_text, chunk=chunk_text)
        response = self._client.generate(
            model=self.settings.model,
            prompt=prompt,
            options={"temperature": self.settings.temperature},
        )
        text = response.get("response", "") if isinstance(response, dict) else str(response)
        return text.strip()

    def generate_blurbs(self, document_text: str, chunks: list[str]) -> list[str]:
        return [self.generate_blurb(document_text, chunk) for chunk in chunks]


def _cap_blurb(blurb: str, max_tokens: int) -> str:
    """Truncate the blurb to the ``max_context_tokens`` cap (chars/4).

    Cuts at the last sentence boundary inside the cap; a blurb with no
    sentence break truncates at the last word boundary instead. Already-
    short blurbs pass through byte-identical.
    """
    limit = max_tokens * _CHARS_PER_TOKEN
    if len(blurb) <= limit:
        return blurb
    head = blurb[:limit]
    sentence_cut = max(head.rfind(". "), head.rfind(".\n"), head.rfind("? "), head.rfind("! "))
    if sentence_cut > 0:
        return head[: sentence_cut + 1].rstrip()
    word_cut = head.rfind(" ")
    if word_cut > 0:
        return head[:word_cut].rstrip()
    return head.rstrip()


def _fixture_key(document_text: str, chunk_text: str, profile: str) -> str:
    """SHA256 fixture key; the profile is part of the hashed material so
    recorded fixtures never collide across prompt profiles."""
    return hashlib.sha256(
        (profile + "\x00" + document_text + "\x00" + chunk_text).encode("utf-8")
    ).hexdigest()

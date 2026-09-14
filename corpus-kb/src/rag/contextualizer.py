"""Anthropic-style Contextual Retrieval blurb generator.

Generates a short LLM blurb situating a chunk within its parent document,
mirroring src/extraction/langextract_backend.py's fixture-keyed,
live_fallback-gated, deterministic-at-temperature-0 pattern. Never raises
into the ingest pipeline: any failure degrades to an empty blurb, which
`coalesce()` treats as blurb-free at both embed and FTS-index time.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, cast

from ollama import Client

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = (
    "Here is the document:\n<document>\n{document}\n</document>\n\n"
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Please give a short succinct context to situate this chunk within the "
    "overall document for the purposes of improving search retrieval of the "
    "chunk. Answer only with the succinct context and nothing else."
)

_MAX_DOC_CHARS = 8000  # bound cost of the prompt sent to the LLM


class ContextGenerator:
    def __init__(self, config: Optional[dict[str, object]] = None) -> None:
        cfg = cast(dict[str, object], (config or {}).get("contextual", {}) or {})
        self.model = str(cfg.get("model", "qwen3:4b"))
        self.base_url = str(cfg.get("base_url", "http://localhost:11434"))
        self.temperature = float(cfg.get("temperature", 0.0))
        fixture_dir = cfg.get("fixture_dir")
        self.fixture_dir = Path(str(fixture_dir)) if fixture_dir else None
        self.live_fallback = bool(cfg.get("live_fallback", False))
        self._client = Client(host=self.base_url)

    def generate_blurb(self, document_text: str, chunk_text: str) -> str:
        key = _sha256(document_text[:_MAX_DOC_CHARS] + "\x00" + chunk_text)
        if self.fixture_dir is not None:
            fixture_path = self.fixture_dir / f"{key}.txt"
            if fixture_path.exists():
                return fixture_path.read_text(encoding="utf-8").strip()
            if not self.live_fallback:
                return ""
        try:
            return self._client_generate(document_text[:_MAX_DOC_CHARS], chunk_text)
        except Exception as exc:  # never raise into ingest
            logger.warning(
                "Contextualizer unavailable: %s; chunk stays blurb-free.", exc
            )
            return ""

    def _client_generate(self, document_text: str, chunk_text: str) -> str:
        prompt = _PROMPT_TEMPLATE.format(document=document_text, chunk=chunk_text)
        response = self._client.generate(
            model=self.model,
            prompt=prompt,
            options={"temperature": self.temperature},
        )
        text = (
            response.get("response", "")
            if isinstance(response, dict)
            else str(response)
        )
        return text.strip()

    def generate_blurbs(self, document_text: str, chunks: list[str]) -> list[str]:
        return [self.generate_blurb(document_text, chunk) for chunk in chunks]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

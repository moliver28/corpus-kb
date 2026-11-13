"""Honest embedder usage accounting for ingest receipts (issue #198).

An embedder whose backend reports its spend (Ollama: the embed response
carries a prompt token count) publishes a per-call ``EmbedUsage`` snapshot
on the embedder instance. Embedders whose backend reports nothing (pgml,
the local late-chunking stack, the fake embedder) publish none — callers
treat the absence as "unknown", never as zero.

Confinement contract (momus F9): a usage receipt is exact only while one
embedder instance serves one caller at a time. Ingest holds this by
construction — ``embedder_selection`` builds one embedder per document.
Research link embeds hold it by discipline — ``link_exchanges`` runs
synchronous embeds on the event loop, so one file's embeds never
interleave with another task's. A memoized or shared instance must never
cross a ``to_thread`` boundary while a tally is open: a thread switch
there can move another caller's spend into this receipt, and no cheap
reader can undo that. ``OllamaEmbedder.pop_usage`` is the take-and-clear
reader; plain ``last_usage`` reads stay legal for single-reader paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EmbedUsage:
    """What one ``embed_batch`` call observed.

    ``prompt_tokens`` is the provider-reported prompt token total for the
    API calls of this batch; None when the provider reported none (a
    cache-only call, a degraded call, or a response without a count).
    ``embedded_texts`` / ``cached_texts`` are the cache-miss / cache-hit
    text counts of the call.
    """

    prompt_tokens: int | None
    embedded_texts: int
    cached_texts: int

    def merged(self, other: EmbedUsage) -> EmbedUsage:
        """Sum two snapshots; the token total is None when either is None.

        A run where any API call went unreported records no token count —
        a partial sum would fake precision.
        """
        tokens = (
            None
            if self.prompt_tokens is None or other.prompt_tokens is None
            else self.prompt_tokens + other.prompt_tokens
        )
        return EmbedUsage(
            prompt_tokens=tokens,
            embedded_texts=self.embedded_texts + other.embedded_texts,
            cached_texts=self.cached_texts + other.cached_texts,
        )


class UsageTally:
    """Merge snapshots across the calls of one run (event-loop confined)."""

    def __init__(self) -> None:
        self._usage: EmbedUsage | None = None

    def add(self, usage: EmbedUsage) -> None:
        """Fold one snapshot into the running total."""
        self._usage = usage if self._usage is None else self._usage.merged(usage)

    @property
    def usage(self) -> EmbedUsage | None:
        """The merged snapshot, or None when nothing was recorded."""
        return self._usage


def embed_usage_section(usage: EmbedUsage, provider: str) -> dict[str, Any]:
    """The typed ``token_usage.embed`` receipt section (issue #198).

    ``prompt_tokens`` appears only when the provider reported a total.
    Key order is fixed, so the serialized receipt is deterministic.

    Degraded-run counting choice (momus F9): a degraded call still
    counts its texts. The embedder answered zero vectors, so the chunks
    hold no usable vectors — but the run did hand those texts to the
    embed stage. ``embedded_texts`` counts them and ``prompt_tokens``
    stays absent; the envelope's ``embedding_mode``/``embedding_note``
    carry the degraded signal, so the receipt never needs a second
    marker.
    """
    section: dict[str, Any] = {
        "provider": provider,
        "embedded_texts": usage.embedded_texts,
        "cached_texts": usage.cached_texts,
    }
    if usage.prompt_tokens is not None:
        section["prompt_tokens"] = usage.prompt_tokens
    return section

"""Claim decomposition + entailment judge for groundedness verification.

Mirrors OllamaEmbedder's structure and degrade-on-failure contract using
Ollama's .chat() (confirmed available). The judge never raises into a
caller: any failure yields an empty decomposition or an "unsupported"
verdict at 0.0 confidence, so verify_answer's abstain path is always safe.

The entailment seams come in two shapes with the same evidence rule
(ONLY the cited texts, no outside knowledge): :meth:`entail` labels one
claim per call, :meth:`entail_batch` labels many claims that share one
evidence slice in ONE call — the batching halves the judge's round
trips and input tokens on the ask path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx
from ollama import Client, ResponseError

logger = logging.getLogger(__name__)

# Bound on every chat call (seconds), matching nli_client.NLI_TIMEOUT_SECONDS:
# the ollama client otherwise waits forever — a wedged server accepts TCP but
# never answers the POST (the 2026-10-09 pytest-suite hang). 120s covers a
# cold qwen3:4b load on CPU; slower than that must DEGRADE per the contract
# below, never hang.
JUDGE_TIMEOUT_SECONDS = 120.0

# The label vocabulary one entail verdict may use.
VALID_LABELS = frozenset({"entailed", "contradicted", "unsupported"})


class OllamaJudge:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = cast(dict[str, Any], (config or {}).get("judge", {}) or {})
        self.model = str(cfg.get("model", "qwen3:4b"))
        self.base_url = str(cfg.get("base_url", "http://localhost:11434"))
        self.max_claims = int(cfg.get("max_claims", 20))
        self._client = Client(host=self.base_url, timeout=JUDGE_TIMEOUT_SECONDS)

    def decompose(self, answer: str) -> list[str]:
        try:
            raw = self._client_chat(
                "Decompose the following answer into a JSON list of short atomic factual claims. "
                "Respond with ONLY a JSON array of strings.",
                answer,
            )
            claims = json.loads(raw)
            if isinstance(claims, list):
                return [str(c) for c in claims][: self.max_claims]
            return []
        except (
            ConnectionError,
            OSError,
            # TransportError covers NetworkError AND TimeoutException AND
            # RemoteProtocolError (the wedged/disconnecting-server lesson
            # from nli_client: a sibling entry is not enough — use the
            # common parent so every transport failure degrades).
            httpx.TransportError,
            ResponseError,
            json.JSONDecodeError,
        ) as exc:
            # ResponseError covers "model not found" (chat model not pulled).
            logger.warning("Judge decompose unavailable: %s", exc)
            return []

    def entail(self, claim: str, cited_texts: list[str]) -> tuple[str, float]:
        evidence = "\n---\n".join(cited_texts)
        try:
            raw = self._client_chat(
                "Given ONLY the evidence below (no outside knowledge), label the claim as one of "
                "entailed, contradicted, or unsupported, and give a confidence 0-1. "
                'Respond with ONLY JSON: {"label": "...", "confidence": 0.0}.\n\n'
                f"Evidence:\n{evidence}",
                claim,
            )
            data = json.loads(raw)
            return self._verdict(data if isinstance(data, dict) else None)
        except (
            ConnectionError,
            OSError,
            httpx.TransportError,
            ResponseError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            logger.warning("Judge entail unavailable: %s", exc)
            return "unsupported", 0.0

    def entail_batch(self, claims: list[str], cited_texts: list[str]) -> list[tuple[str, float]]:
        """Label many claims against one evidence slice in ONE chat call.

        Claims that share one evidence slice share one call, so a full
        ask costs a handful of round trips instead of one per claim. The
        response is a JSON list whose objects carry the claim number as
        "n". A claim with a missing or malformed entry degrades to
        ("unsupported", 0.0) — the same rule a failed single entail
        follows — and a transport or parse failure degrades the whole
        batch. Never raises.
        """
        evidence = "\n---\n".join(cited_texts)
        numbered = "\n".join(f"{index}. {claim}" for index, claim in enumerate(claims, 1))
        try:
            raw = self._client_chat(
                "Given ONLY the evidence below (no outside knowledge), label EACH numbered claim "
                "as one of entailed, contradicted, or unsupported, and give a confidence 0-1 "
                "for each. Respond with ONLY a JSON list, one object per claim: "
                '[{"n": 1, "label": "...", "confidence": 0.0}]. '
                'The "n" key is the claim number.\n\n'
                f"Evidence:\n{evidence}",
                f"Claims:\n{numbered}",
            )
            entries = _entries_by_claim(json.loads(raw), len(claims))
            return [self._verdict(entries.get(position)) for position in range(1, len(claims) + 1)]
        except (
            ConnectionError,
            OSError,
            httpx.TransportError,
            ResponseError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            logger.warning("Judge entail_batch unavailable: %s", exc)
            return [("unsupported", 0.0)] * len(claims)

    def _verdict(self, data: dict[str, object] | None) -> tuple[str, float]:
        """One parsed verdict; missing or malformed fields degrade to
        the unsupported/0.0 pair the caller can trust."""
        if not isinstance(data, dict) or not data:
            return "unsupported", 0.0
        label = str(data.get("label", "unsupported"))
        if label not in VALID_LABELS:
            label = "unsupported"
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return "unsupported", 0.0
        if not 0.0 <= confidence <= 1.0:
            confidence = 0.0
        return label, confidence

    def _client_chat(self, system: str, user: str) -> str:
        response = self._client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0},
        )
        # ollama>=0.4 returns a ChatResponse object whose message carries
        # attributes; older dict-shaped responses stay supported.
        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message")
        content = getattr(message, "content", None) if message is not None else None
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return str(content) if content is not None else "[]"


def _entries_by_claim(data: object, claim_count: int) -> dict[int, dict[str, object]]:
    """Index one batch response by claim number; junk entries are dropped.

    A number is the int 1..claim_count (a digit string is accepted, since
    models often quote JSON numbers); out-of-range numbers and entries
    without a usable number are ignored, and their claims degrade.
    """
    entries: dict[int, dict[str, object]] = {}
    if not isinstance(data, list):
        return entries
    for entry in data:
        if not isinstance(entry, dict):
            continue
        number = entry.get("n")
        if isinstance(number, bool):
            continue
        if isinstance(number, int) and 1 <= number <= claim_count:
            entries[number] = entry
        elif isinstance(number, str) and number.strip().isdigit():
            position = int(number.strip())
            if 1 <= position <= claim_count:
                entries[position] = entry
    return entries

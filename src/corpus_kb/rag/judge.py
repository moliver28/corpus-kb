"""Claim decomposition + entailment judge for groundedness verification.

Mirrors OllamaEmbedder's structure and degrade-on-failure contract using
Ollama's .chat() (confirmed available). The judge never raises into a
caller: any failure yields an empty decomposition or an "unsupported"
verdict at 0.0 confidence, so verify_answer's abstain path is always safe.
"""

from __future__ import annotations

import json
import logging
from typing import cast

import httpx
from ollama import Client, ResponseError

logger = logging.getLogger(__name__)

# Bound on every chat call (seconds), matching nli_client.NLI_TIMEOUT_SECONDS:
# the ollama client otherwise waits forever — a wedged server accepts TCP but
# never answers the POST (the 2026-10-09 pytest-suite hang). 120s covers a
# cold qwen3:4b load on CPU; slower than that must DEGRADE per the contract
# below, never hang.
JUDGE_TIMEOUT_SECONDS = 120.0


class OllamaJudge:
    def __init__(self, config: dict[str, object] | None = None) -> None:
        cfg = cast(dict[str, object], (config or {}).get("judge", {}) or {})
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
            label = str(data.get("label", "unsupported"))
            if label not in {"entailed", "contradicted", "unsupported"}:
                label = "unsupported"
            confidence = float(data.get("confidence", 0.0))
            return label, confidence
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

    def _client_chat(self, system: str, user: str) -> str:
        response = self._client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0},
        )
        message = response.get("message", {}) if isinstance(response, dict) else {}
        return str(message.get("content", "[]"))

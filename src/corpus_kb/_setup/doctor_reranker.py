"""Reranker-backend truth check for ``corpus-kb doctor``.

Same contract as the other configuration-truth checks (read-only, WARN at
worst): the check reports which scorer ``reranking.backend`` selects and
probes it live.

* ``llamacpp``: GET ``{llamacpp_url}/health`` (llama-server exposes it).
  200 means the rerank stage has a real cross-encoder behind it.
* null/``generative``: GET ``{base_url}/api/tags`` and look for the
  configured model. The detail carries the honest note that Ollama
  0.31.1 cannot serve reranker GGUFs (no rerank endpoint, generate
  refused), with docs/ADMIN.md pointing at the llama.cpp recipe.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from corpus_kb._setup.doctor_research import (
    STATUS_OK,
    STATUS_WARN,
    ResearchCheck,
)
from corpus_kb.rag.rerank_settings import load_rerank_settings

DOC_ADMIN = "docs/ADMIN.md"

PROBE_TIMEOUT_SECONDS = 3.0

CHECK_NAME = "reranker backend"


async def reranker_backend_check(config: dict[str, Any]) -> ResearchCheck:
    """Probe the configured reranker backend; never raises."""
    settings = load_rerank_settings(cast("dict[str, object]", config))
    if not settings.enabled:
        return ResearchCheck(
            name=CHECK_NAME,
            status=STATUS_OK,
            detail="reranking disabled (reranking.enabled: false)",
            doc=DOC_ADMIN,
        )
    if settings.backend == "llamacpp":
        return await _llamacpp_health_check(settings.llamacpp_url, settings.model)
    return await _ollama_model_check(settings.base_url, settings.model)


async def _llamacpp_health_check(url: str, model: str) -> ResearchCheck:
    """GET {url}/health — llama-server answers 200 when ready to rank."""
    health_url = f"{url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(health_url)
    except (httpx.HTTPError, OSError) as exc:
        return ResearchCheck(
            name=CHECK_NAME,
            status=STATUS_WARN,
            detail=(
                f"llama-server not reachable at {health_url} ({exc}); the rerank "
                "stage degrades to the fused RRF order"
            ),
            fix="start: llama-server -m <rank-gguf> --port 8080 (recipe in docs/ADMIN.md)",
            doc=DOC_ADMIN,
        )
    if response.status_code != 200:
        # llama-server answers 503 while the model is still loading.
        return ResearchCheck(
            name=CHECK_NAME,
            status=STATUS_WARN,
            detail=(
                f"llama-server at {health_url} answered HTTP {response.status_code} "
                "(503 means the model is still loading)"
            ),
            fix="wait for the model to load, then re-run: corpus-kb doctor",
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name=CHECK_NAME,
        status=STATUS_OK,
        detail=(
            f"llama-server healthy at {health_url}; backend llamacpp scores "
            f"true cross-encoder values (model {model!r}, POST /v1/rerank)"
        ),
        doc=DOC_ADMIN,
    )


async def _ollama_model_check(base_url: str, model: str) -> ResearchCheck:
    """Is the configured model present in Ollama, with the honest caveat."""
    tags_url = f"{base_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(tags_url)
        names = _model_names(response.json())
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return ResearchCheck(
            name=CHECK_NAME,
            status=STATUS_WARN,
            detail=(
                f"Ollama not reachable at {tags_url} ({exc}); the generative "
                "rerank stage degrades to the fused RRF order"
            ),
            fix="start Ollama and pull the model, or run llama-server (docs/ADMIN.md)",
            doc=DOC_ADMIN,
        )
    if model not in names:
        return ResearchCheck(
            name=CHECK_NAME,
            status=STATUS_WARN,
            detail=(
                f"model {model!r} not found in Ollama at {base_url}; the rerank "
                "stage degrades to the fused RRF order"
            ),
            fix=(
                f"ollama pull {model} (judgment mode), or run llama-server for "
                "true cross-encoder scores (recipe in docs/ADMIN.md)"
            ),
            doc=DOC_ADMIN,
        )
    return ResearchCheck(
        name=CHECK_NAME,
        status=STATUS_OK,
        detail=(
            f"model {model!r} present in Ollama; grading is generative-binary. "
            "Reranker GGUFs get no true scores on Ollama 0.31.1 (no rerank "
            "endpoint) — docs/ADMIN.md has the llama.cpp cross-encoder recipe"
        ),
        doc=DOC_ADMIN,
    )


def _model_names(body: object) -> set[str]:
    """Model names off an ``/api/tags`` payload; unreadable shapes yield {}."""
    if not isinstance(body, dict):
        return set()
    models = body.get("models")
    if not isinstance(models, list):
        return set()
    names: set[str] = set()
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


__all__ = ["reranker_backend_check"]

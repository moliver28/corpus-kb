"""Reranker-backend truth check for ``corpus-kb doctor``.

Same contract as the other configuration-truth checks (read-only, WARN at
worst): the check reports which scorer ``reranking.backend`` selects and
probes it live.

* ``llamacpp``: GET ``{url}/health``, then POST one sanity pair to
  ``/v1/rerank``. A broken GGUF conversion (no classifier tensor) serves
  200s with inverted or near-zero scores; the verdict reports WARN.
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

# Sanity pair (functional probe): a healthy /health says nothing about score
# quality. It fails when the unrelated text wins, or both scores sit at zero.
SANITY_TIMEOUT_SECONDS = 5.0
SANITY_QUERY = "capital of France"
SANITY_RELEVANT_DOC = "Paris is the capital of France."
SANITY_IRRELEVANT_DOC = "Mitochondria produce energy in cells."
SANITY_ZERO_FLOOR = 1e-10

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
    """GET {url}/health, then POST a sanity pair to {url}/v1/rerank."""
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
    problem = await _sanity_pair_probe(url, model)
    if problem is not None:
        return problem
    return ResearchCheck(
        name=CHECK_NAME,
        status=STATUS_OK,
        detail=(
            f"llama-server healthy at {health_url}; the sanity pair scored as "
            f"expected; backend llamacpp returns true cross-encoder values "
            f"(model {model!r}, POST /v1/rerank)"
        ),
        doc=DOC_ADMIN,
    )


async def _sanity_pair_probe(url: str, model: str) -> ResearchCheck | None:
    """POST one obvious pair to ``{url}/v1/rerank``; None means sane.

    Never raises: every failure path is a degraded WARN the doctor prints.
    """
    rerank_url = f"{url.rstrip('/')}/v1/rerank"
    documents = [SANITY_RELEVANT_DOC, SANITY_IRRELEVANT_DOC]
    payload: dict[str, object] = {"model": model, "query": SANITY_QUERY, "documents": documents}
    try:
        async with httpx.AsyncClient(timeout=SANITY_TIMEOUT_SECONDS) as client:
            response = await client.post(rerank_url, json=payload)
    except (httpx.HTTPError, OSError) as exc:
        return _probe_degraded(
            f"llama-server passed the health probe but the rerank probe "
            f"failed at {rerank_url} ({exc});"
        )
    if response.status_code != 200:
        return _probe_degraded(
            f"llama-server passed the health probe but {rerank_url} answered "
            f"HTTP {response.status_code};"
        )
    try:
        body = response.json()
    except ValueError:
        body = None
    scores = _pair_scores(body)
    if scores is None:
        return _sanity_failure(rerank_url, "the rerank probe returned no usable scores")
    relevant, unrelated = scores
    if relevant < SANITY_ZERO_FLOOR and unrelated < SANITY_ZERO_FLOOR:
        return _sanity_failure(rerank_url, "the reranker gave both texts near-zero scores")
    if relevant <= unrelated:
        return _sanity_failure(rerank_url, "the reranker gave the relevant text a lower score")
    return None


def _probe_degraded(detail: str) -> ResearchCheck:
    """Degraded WARN when the rerank POST itself fails (transport or HTTP)."""
    return ResearchCheck(
        name=CHECK_NAME,
        status=STATUS_WARN,
        detail=f"{detail} the rerank stage degrades to the fused RRF order",
        fix=(
            "start llama-server with --reranking --pooling rank --embedding "
            "(recipe in docs/ADMIN.md)"
        ),
        doc=DOC_ADMIN,
    )


def _sanity_failure(rerank_url: str, reason: str) -> ResearchCheck:
    """Degraded WARN for a failed pair verdict, with the GGUF rebuild fix."""
    return ResearchCheck(
        name=CHECK_NAME,
        status=STATUS_WARN,
        detail=(
            f"sanity pair failed on {rerank_url}: {reason}. The rerank stage "
            "degrades to the fused RRF order"
        ),
        fix="rebuild the rank GGUF so it keeps the classifier tensor (docs/ADMIN.md)",
        doc=DOC_ADMIN,
    )


def _pair_scores(body: object) -> tuple[float, float] | None:
    """(relevant, unrelated) scores by index (never response position); None if unreadable."""
    if not isinstance(body, dict):
        return None
    results = body.get("results")
    if not isinstance(results, list):
        return None
    by_index: dict[int, float] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        value = entry.get("relevance_score")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        by_index[index] = float(value)
    if 0 not in by_index or 1 not in by_index:
        return None
    return by_index[0], by_index[1]


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

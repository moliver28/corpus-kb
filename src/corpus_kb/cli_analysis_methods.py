"""The analysis method front doors (AMR program, wave 2).

Top-level commands registered on the main app (see ``register``): the
twelve register-backed method ops, kebab-case, one handler each — the
same shared function the MCP + HTTP + socket surfaces call.

HONEST EXIT CODES (stable for scripting): ``0`` the command answered
(including the honest no-op outcomes: disabled flag, empty corpus,
insufficient data), ``1`` the command failed or the request was
malformed, ``3`` configuration state (feature unavailable, database
unreachable). Mirrors ``cli_analysis.py``. All help text is plain
English (ASD-STE100).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3

# Honest no-op statuses: the command answered, there is nothing to fix.
_OK_STATUSES = ("success", "disabled", "empty", "insufficient_data")


def register(app: typer.Typer) -> None:
    """Attach the twelve method commands to the main Typer app."""
    app.command("analysis-select-method")(analysis_select_method)
    app.command("keyphrases-extract")(keyphrases_extract)
    app.command("lexical-classify")(lexical_classify)
    app.command("neighbors-classify")(neighbors_classify)
    app.command("centroids-classify")(centroids_classify)
    app.command("clusters-inspect")(clusters_inspect)
    app.command("duplicates-screen")(duplicates_screen)
    app.command("outliers-rank")(outliers_rank)
    app.command("nli-classify")(nli_classify)
    app.command("contradictions-screen")(contradictions_screen)
    app.command("sentiment-screen")(sentiment_screen)
    app.command("active-learn-next")(active_learn_next)


def _exit_for(result: dict[str, object]) -> int:
    """Map a method-op envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    if status in _OK_STATUSES:
        return EXIT_OK
    if status == "unavailable":
        return EXIT_CONFIG
    return EXIT_FAILED


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"analysis-methods: {result.get('status', 'error')}")
    for key in ("reason", "remedy"):
        value = result.get(key)
        if value:
            print(f"  {key}: {value}")


async def _flow(module_name: str, op_name: str, kwargs: dict[str, Any], json_output: bool) -> int:
    """One command flow: load config, open the pool, call, emit, map."""
    from corpus_kb.errors import CorpusError

    module = __import__(f"corpus_kb.tools.{module_name}", fromlist=[op_name])
    handler: Callable[..., Awaitable[dict[str, object]]] = getattr(module, op_name)
    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await handler(pool, cfg, **kwargs)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        remediation = getattr(exc, "remediation", None)
        if remediation:
            print(f"  {remediation}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_IDS_OPT = typer.Option(None, "--chunk-id", help="Limit to these chunk ids (repeatable)")
_INPUTS_OPT = typer.Option(None, "--input", help="Available input kinds (repeatable)")
_LABELS_OPT = typer.Option(None, "--label", help="Candidate labels (repeatable)")


def _ids(values: list[str] | None) -> list[str] | None:
    """Absent when no ids were passed (the handlers' own default)."""
    return [str(value) for value in values] if values else None


def analysis_select_method(
    task_description: str = typer.Argument(..., help="The research task, one sentence"),
    output_family: str = typer.Option(
        None, "--output-family", help="labels|themes|scores|structures|text|errors|recommendations"
    ),
    inputs: list[str] = _INPUTS_OPT,
    max_complexity_tier: str = typer.Option(
        None, "--max-complexity-tier", help="baseline|standard|advanced"
    ),
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank the register's methods for the stated task."""
    kwargs = {
        "task_description": task_description,
        "output_family": output_family,
        "inputs_available": _ids(inputs),
        "max_complexity_tier": max_complexity_tier,
    }
    return _run_sync(
        lambda: _flow("method_select_tools", "analysis_select_method", kwargs, json_output)
    )


def keyphrases_extract(
    variant: str = typer.Option("tfidf", "--variant", help="tfidf|embedding|llm"),
    limit: int = typer.Option(20, "--limit", help="Max keyphrases to return"),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank keyphrase candidates over the selected chunks."""
    kwargs = {"variant": variant, "limit": limit, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(lambda: _flow("keyphrase_tools", "keyphrases_extract", kwargs, json_output))


def lexical_classify(chunk_ids: list[str] = _IDS_OPT, json_output: bool = _JSON_OPT) -> int:
    """Train on the stored labels; score the corpus (TF-IDF + logistic)."""
    kwargs = {"chunk_ids": _ids(chunk_ids)}
    return _run_sync(lambda: _flow("lexical_tools", "lexical_classify", kwargs, json_output))


def neighbors_classify(
    k: int = typer.Option(5, "--k", help="Neighbors per vote"),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """kNN-vote labels over the labeled chunks."""
    kwargs = {"k": k, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(lambda: _flow("neighbor_tools", "neighbors_classify", kwargs, json_output))


def centroids_classify(
    abstain_margin: float = typer.Option(
        0.05, "--abstain-margin", help="Abstain below this margin"
    ),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Nearest-centroid labels with recorded abstentions."""
    kwargs = {"abstain_margin": abstain_margin, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(lambda: _flow("centroid_tools", "centroids_classify", kwargs, json_output))


def clusters_inspect(
    min_cluster_size: int = typer.Option(
        None, "--min-cluster-size", help="HDBSCAN minimum (default 5)"
    ),
    sample_cap: int = typer.Option(None, "--sample-cap", help="Seeded subsample cap"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report the corpus cluster structure (sizes, noise, exemplars)."""
    kwargs = {"min_cluster_size": min_cluster_size, "sample_cap": sample_cap}
    return _run_sync(lambda: _flow("cluster_tools", "clusters_inspect", kwargs, json_output))


def duplicates_screen(
    threshold: float = typer.Option(0.98, "--threshold", help="Cosine threshold"),
    limit: int = typer.Option(100, "--limit", help="Max pairs to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report near-duplicate candidate pairs."""
    kwargs = {"threshold": threshold, "limit": limit}
    return _run_sync(lambda: _flow("vector_screen_tools", "duplicates_screen", kwargs, json_output))


def outliers_rank(
    k: int = typer.Option(10, "--k", help="Neighbor rank for the distance"),
    limit: int = typer.Option(20, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank chunks by distance to the k-th nearest neighbor."""
    kwargs = {"k": k, "limit": limit}
    return _run_sync(lambda: _flow("vector_screen_tools", "outliers_rank", kwargs, json_output))


def nli_classify(
    labels: list[str] = _LABELS_OPT,
    chunk_ids: list[str] = _IDS_OPT,
    limit: int = typer.Option(50, "--limit", help="Max chunks to judge"),
    model: str = typer.Option(None, "--model", help="Override the NLI model tag"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Zero-shot labels via entailment judgments."""
    kwargs = {
        "labels": [str(label) for label in labels] if labels else [],
        "chunk_ids": _ids(chunk_ids),
        "limit": limit,
        "model": model,
    }
    return _run_sync(lambda: _flow("nli_tools", "nli_classify", kwargs, json_output))


def contradictions_screen(
    candidate_k: int = typer.Option(5, "--candidate-k", help="Neighbor candidates per chunk"),
    limit: int = typer.Option(50, "--limit", help="Max candidate pairs to judge"),
    model: str = typer.Option(None, "--model", help="Override the NLI model tag"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report candidate pairs the NLI model marks CONTRADICT."""
    kwargs = {"candidate_k": candidate_k, "limit": limit, "model": model}
    return _run_sync(lambda: _flow("nli_tools", "contradictions_screen", kwargs, json_output))


def sentiment_screen(
    chunk_ids: list[str] = _IDS_OPT,
    limit: int = typer.Option(None, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Screen chunks with the preset lexicon (vote aggregation)."""
    kwargs = {"chunk_ids": _ids(chunk_ids), "limit": limit}
    return _run_sync(lambda: _flow("sentiment_tools", "sentiment_screen", kwargs, json_output))


def active_learn_next(
    strategy: str = typer.Option("margin", "--strategy", help="margin|entropy|random_incumbent"),
    batch: int = typer.Option(10, "--batch", help="Batch size to rank"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank stored predictions for the next labeling batch."""
    kwargs = {"strategy": strategy, "batch": batch}
    return _run_sync(lambda: _flow("active_learn_tools", "active_learn_next", kwargs, json_output))

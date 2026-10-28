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
from typing import Any

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, flow

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli.cli_ingest import _run_sync

# Module-level option singletons (B008): repeatable list options must not
# call typer.Option inline.
_IDS_OPT = typer.Option(None, "--chunk-id", help="Limit to these chunk ids (repeatable)")
_INPUTS_OPT = typer.Option(None, "--input", help="Available input kinds (repeatable)")
_LABELS_OPT = typer.Option(None, "--label", help="Candidate labels (repeatable)")


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


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"analysis-methods: {result.get('status', 'error')}")
    for key in ("reason", "remedy"):
        value = result.get(key)
        if value:
            print(f"  {key}: {value}")


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
    kwargs: dict[str, Any] = {
        "task_description": task_description,
        "output_family": output_family,
        "inputs_available": _ids(inputs),
        "max_complexity_tier": max_complexity_tier,
    }
    return _run_sync(
        lambda: flow("method_select_tools", "analysis_select_method", kwargs, json_output, _emit)
    )


def keyphrases_extract(
    variant: str = typer.Option("tfidf", "--variant", help="tfidf|embedding|llm"),
    limit: int = typer.Option(20, "--limit", help="Max keyphrases to return"),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank keyphrase candidates over the selected chunks."""
    kwargs: dict[str, Any] = {"variant": variant, "limit": limit, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(
        lambda: flow("keyphrase_tools", "keyphrases_extract", kwargs, json_output, _emit)
    )


def lexical_classify(chunk_ids: list[str] = _IDS_OPT, json_output: bool = _JSON_OPT) -> int:
    """Train on the stored labels; score the corpus (TF-IDF + logistic)."""
    kwargs: dict[str, Any] = {"chunk_ids": _ids(chunk_ids)}
    return _run_sync(lambda: flow("lexical_tools", "lexical_classify", kwargs, json_output, _emit))


def neighbors_classify(
    k: int = typer.Option(5, "--k", help="Neighbors per vote"),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """kNN-vote labels over the labeled chunks."""
    kwargs: dict[str, Any] = {"k": k, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(
        lambda: flow("neighbor_tools", "neighbors_classify", kwargs, json_output, _emit)
    )


def centroids_classify(
    abstain_margin: float = typer.Option(
        0.05, "--abstain-margin", help="Abstain below this margin"
    ),
    chunk_ids: list[str] = _IDS_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Nearest-centroid labels with recorded abstentions."""
    kwargs: dict[str, Any] = {"abstain_margin": abstain_margin, "chunk_ids": _ids(chunk_ids)}
    return _run_sync(
        lambda: flow("centroid_tools", "centroids_classify", kwargs, json_output, _emit)
    )


def clusters_inspect(
    min_cluster_size: int = typer.Option(
        None, "--min-cluster-size", help="HDBSCAN minimum (default 5)"
    ),
    sample_cap: int = typer.Option(None, "--sample-cap", help="Seeded subsample cap"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report the corpus cluster structure (sizes, noise, exemplars)."""
    kwargs: dict[str, Any] = {"min_cluster_size": min_cluster_size, "sample_cap": sample_cap}
    return _run_sync(lambda: flow("cluster_tools", "clusters_inspect", kwargs, json_output, _emit))


def duplicates_screen(
    threshold: float = typer.Option(0.98, "--threshold", help="Cosine threshold"),
    limit: int = typer.Option(100, "--limit", help="Max pairs to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report near-duplicate candidate pairs."""
    kwargs: dict[str, Any] = {"threshold": threshold, "limit": limit}
    return _run_sync(
        lambda: flow("vector_screen_tools", "duplicates_screen", kwargs, json_output, _emit)
    )


def outliers_rank(
    k: int = typer.Option(10, "--k", help="Neighbor rank for the distance"),
    limit: int = typer.Option(20, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank chunks by distance to the k-th nearest neighbor."""
    kwargs: dict[str, Any] = {"k": k, "limit": limit}
    return _run_sync(
        lambda: flow("vector_screen_tools", "outliers_rank", kwargs, json_output, _emit)
    )


def nli_classify(
    labels: list[str] = _LABELS_OPT,
    chunk_ids: list[str] = _IDS_OPT,
    limit: int = typer.Option(50, "--limit", help="Max chunks to judge"),
    model: str = typer.Option(None, "--model", help="Override the NLI model tag"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Zero-shot labels via entailment judgments."""
    kwargs: dict[str, Any] = {
        "labels": [str(label) for label in labels] if labels else [],
        "chunk_ids": _ids(chunk_ids),
        "limit": limit,
        "model": model,
    }
    return _run_sync(lambda: flow("nli_tools", "nli_classify", kwargs, json_output, _emit))


def contradictions_screen(
    candidate_k: int = typer.Option(5, "--candidate-k", help="Neighbor candidates per chunk"),
    limit: int = typer.Option(50, "--limit", help="Max candidate pairs to judge"),
    model: str = typer.Option(None, "--model", help="Override the NLI model tag"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Report candidate pairs the NLI model marks CONTRADICT."""
    kwargs: dict[str, Any] = {"candidate_k": candidate_k, "limit": limit, "model": model}
    return _run_sync(lambda: flow("nli_tools", "contradictions_screen", kwargs, json_output, _emit))


def sentiment_screen(
    chunk_ids: list[str] = _IDS_OPT,
    limit: int = typer.Option(None, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Screen chunks with the preset lexicon (vote aggregation)."""
    kwargs: dict[str, Any] = {"chunk_ids": _ids(chunk_ids), "limit": limit}
    return _run_sync(
        lambda: flow("sentiment_tools", "sentiment_screen", kwargs, json_output, _emit)
    )


def active_learn_next(
    strategy: str = typer.Option("margin", "--strategy", help="margin|entropy|random_incumbent"),
    batch: int = typer.Option(10, "--batch", help="Batch size to rank"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Rank stored predictions for the next labeling batch."""
    kwargs: dict[str, Any] = {"strategy": strategy, "batch": batch}
    return _run_sync(
        lambda: flow("active_learn_tools", "active_learn_next", kwargs, json_output, _emit)
    )

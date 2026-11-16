"""Encoded lookup tables for the doctor journey checks (novice-UX W3).

The register carries method ids and ops, not CLI commands or build
products, so the journey encodes the two small bridges here — once, with
drift-guard tests that pin them against the live register (every
implemented op needs an entry; every prerequisite row needs a build
product) and against the surface registry (each entry equals the
declared ``cli_path``). Adding an analysis op or a prerequisite means
one row here plus the register row — the tests name the gap.
"""

from __future__ import annotations

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_dataflow import artifact_prerequisites

OP_CLI: dict[str, str] = {
    # Method op -> CLI command path. Verified against the ``app.command``
    # registrations under ``src/corpus_kb/cli/``. The one entry without a
    # surface-registry row is ``ingest_text``: its CLI leg is
    # ``cli_ingest_text.py``'s ``ingest-text`` command.
    "analyze_run": "analyze-run",
    "labeling_build": "labeling-build",
    "topics_build": "topics-build",
    "keyphrases_extract": "keyphrases-extract",
    "clusters_inspect": "clusters-inspect",
    "centroids_classify": "centroids-classify",
    "neighbors_classify": "neighbors-classify",
    "lexical_classify": "lexical-classify",
    "classification_build": "classification-build",
    "nli_classify": "nli-classify",
    "active_learn_next": "active-learn-next",
    "search": "search",
    "ask": "ask",
    "ingest_text": "ingest-text",
    "sentiment_screen": "sentiment-screen",
    "contradictions_screen": "contradictions-screen",
    "duplicates_screen": "duplicates-screen",
    "outliers_rank": "outliers-rank",
}

# Build op -> (the state key it fills, the plain-English action). A build
# method is the next step only while its product does not exist yet.
BUILD_PRODUCTS: dict[str, tuple[str, str]] = {
    "labeling_build": ("labels", "build labels"),
    "topics_build": ("topics", "build topics"),
    "classification_build": ("predictions", "build predictions"),
}

# Input kinds the journey cannot probe: the user supplies them per call.
UNPROBED_INPUTS = frozenset({"seed_labels", "ontology"})

# Probed input kind -> the JourneyState field that answers it.
INPUT_STATE: dict[str, str] = {
    "raw_text": "chunks",
    "embeddings": "vectors",
    "labels": "labels",
    "predictions": "predictions",
    "timestamps": "dated_documents",
}


def journey_prerequisites(register: MethodRegister, method_id: str) -> list[str]:
    """The journey's gating view: artifact prereqs that fill a build product.

    Artifact prerequisites whose producer op is in ``BUILD_PRODUCTS``;
    every other producer (search, ask, screens) never gates the journey.
    """
    by_id = {row.id: row for row in register.methods}
    return [
        pid
        for pid in artifact_prerequisites(register.methods, method_id)
        if by_id[pid].op in BUILD_PRODUCTS
    ]


__all__ = ["BUILD_PRODUCTS", "INPUT_STATE", "OP_CLI", "UNPROBED_INPUTS", "journey_prerequisites"]

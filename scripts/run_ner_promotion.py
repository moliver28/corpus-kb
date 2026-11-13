"""Run the NER promotion harness: the gliner backend vs the default chain.

Usage (repo root, installed package; worktree checkouts need
PYTHONPATH=<worktree>/src so ``corpus_kb`` resolves to the worktree):

    python scripts/run_ner_promotion.py extract --system candidate \
        --gold GOLD.jsonl --predictions candidate.jsonl
    python scripts/run_ner_promotion.py extract --system baseline \
        --gold GOLD.jsonl --predictions baseline.jsonl
    python scripts/run_ner_promotion.py score --gold GOLD.jsonl \
        --candidate-predictions candidate.jsonl \
        --baseline-predictions baseline.jsonl --out report.json \
        [--seed 42] [--resamples 10000]

Extraction and scoring are separate stages ON PURPOSE: each system runs
in its own process, so the two GLiNER-class models never share memory
(two resident copies have segfaulted under Windows), and the scoring
stage needs no torch at all.

Systems:

* candidate: ``extraction.backend: gliner`` (GLiNER2, opt-in)
* baseline:  ``extraction.backend: ml`` (the shipped default chain)

Scores come from ``corpus_kb.extraction.eval`` (exact span identity:
doc_id, start, end, label). The CI is a seeded paired bootstrap with the
document as the pairing unit. The plan-pinned gate promotes when
micro F1 >= 0.80 AND >= baseline + 0.02.

The report JSON carries counts and scores only — never document text.
Gold and prediction files carry corpus text and stay local.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from corpus_kb.config import load_config
from corpus_kb.extraction import Extractor, create_extractor
from corpus_kb.extraction.eval import (
    GoldDoc,
    GoldSpan,
    Scored,
    load_gold,
    paired_bootstrap,
    promotion_gate,
    score_corpus,
)
from corpus_kb.extraction.gliner_backend import _MENTION_SYNONYMS
from corpus_kb.tools.ingest_common import ontology as load_ontology_from_config
from corpus_kb.utils.models import Chunk

#: gliner ontology type -> the mention type it came from (first wins).
_INVERSE_MENTION: dict[str, str] = {}
for _mention, _types in _MENTION_SYNONYMS.items():
    for _type in _types:
        _INVERSE_MENTION.setdefault(_type, _mention)


def _package_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "not installed"


def _entity_span(entity: object, text_length: int) -> GoldSpan | None:
    """One entity as an eval span, or None when it cannot score."""
    start = getattr(entity, "source_start_char", None)
    end = getattr(entity, "source_end_char", None)
    if start is None or end is None:
        return None
    start_i, end_i = int(start), int(end)
    if not 0 <= start_i < end_i <= text_length:
        return None
    metadata = getattr(entity, "metadata", {})
    label = metadata.get("gliner_type") if isinstance(metadata, dict) else None
    if not isinstance(label, str) or not label:
        label = _INVERSE_MENTION.get(str(getattr(entity, "entity_type", "")), "")
    if not label:
        return None
    return GoldSpan(start=start_i, end=end_i, label=label)


def _predict(
    extractor: Extractor, gold: list[GoldDoc], config: dict[str, object]
) -> tuple[dict[str, list[GoldSpan]], int]:
    """Span predictions per doc_id plus the count of unscorable entities."""
    docs = [
        Chunk(
            chunk_id=doc.doc_id,
            document_id=doc.doc_id,
            text=doc.text,
            source_type="text",
        )
        for doc in gold
    ]
    text_len = {doc.doc_id: len(doc.text) for doc in gold}
    entities, _relations, _stats = extractor.extract(
        docs, load_ontology_from_config(config), "ner-promotion-eval"
    )
    by_doc: dict[str, list[GoldSpan]] = {doc.doc_id: [] for doc in gold}
    skipped = 0
    for entity in entities:
        chunk_id = str(getattr(entity, "chunk_id", ""))
        if chunk_id not in by_doc:
            skipped += 1
            continue
        span = _entity_span(entity, text_len[chunk_id])
        if span is None:
            skipped += 1
            continue
        by_doc[chunk_id].append(span)
    return by_doc, skipped


def _both_configs(config: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """(candidate, baseline) configs: gliner vs the shipped ml default."""
    extraction = config.get("extraction") if isinstance(config.get("extraction"), dict) else {}
    gliner = extraction.get("gliner") if isinstance(extraction.get("gliner"), dict) else {}
    candidate = dict(config)
    candidate["extraction"] = {
        **extraction,
        "backend": "gliner",
        "gliner": {**gliner, "enabled": True},
    }
    baseline = dict(config)
    baseline["extraction"] = {**extraction, "backend": "ml"}
    return candidate, baseline


def _write_predictions(path: str, gold: list[GoldDoc], spans: dict[str, list[GoldSpan]]) -> None:
    """Per-doc spans for one system: one JSON object per gold doc."""
    with Path(path).open("w", encoding="utf-8") as f:
        for doc in gold:
            f.write(
                json.dumps(
                    {
                        "doc_id": doc.doc_id,
                        "spans": [dataclasses.asdict(s) for s in spans[doc.doc_id]],
                    }
                )
                + "\n"
            )


def _read_predictions(path: str) -> dict[str, list[GoldSpan]]:
    """Inverse of ``_write_predictions`` for the torch-free score stage."""
    spans: dict[str, list[GoldSpan]] = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            doc_id = str(raw["doc_id"])
            spans[doc_id] = [
                GoldSpan(start=int(s["start"]), end=int(s["end"]), label=str(s["label"]))
                for s in raw.get("spans", [])
            ]
    return spans


def _run_extract(args: argparse.Namespace) -> int:
    gold = load_gold(args.gold)
    if args.limit:
        gold = gold[: args.limit]
    if not gold:
        print("ERROR: the gold set is empty")
        return 1
    config = dict(load_config())
    candidate_cfg, baseline_cfg = _both_configs(config)
    system_cfg = candidate_cfg if args.system == "candidate" else baseline_cfg
    started = time.perf_counter()
    spans, skipped = _predict(create_extractor(system_cfg), gold, system_cfg)
    _write_predictions(args.predictions, gold, spans)
    total = sum(len(v) for v in spans.values())
    print(
        f"system={args.system} docs={len(gold)} spans={total} "
        f"skipped_entities={skipped} runtime={round(time.perf_counter() - started, 1)}s"
    )
    print(f"predictions: {args.predictions}")
    return 0


def _run_score(args: argparse.Namespace) -> int:
    gold = load_gold(args.gold)
    if not gold:
        print("ERROR: the gold set is empty")
        return 1
    started = time.perf_counter()
    candidate = _read_predictions(args.candidate_predictions)
    baseline = _read_predictions(args.baseline_predictions)
    cand_report = score_corpus(gold, candidate)
    base_report = score_corpus(gold, baseline)
    bootstrap = paired_bootstrap(
        gold, candidate, baseline, resamples=args.resamples, seed=args.seed
    )
    gate = promotion_gate(cand_report.micro.f1, base_report.micro.f1)
    seconds = round(time.perf_counter() - started, 1)
    config = dict(load_config())
    extraction_block = config.get("extraction")
    gliner_block = extraction_block.get("gliner", {}) if isinstance(extraction_block, dict) else {}
    report: dict[str, object] = {
        "python": platform.python_version(),
        "packages": {
            name: _package_version(name) for name in ("corpus-kb", "gliner2", "gliner", "torch")
        },
        "candidate": {
            "backend": "gliner",
            "model": gliner_block.get("model") if isinstance(gliner_block, dict) else None,
            "revision": gliner_block.get("revision") if isinstance(gliner_block, dict) else None,
            "min_span_confidence": 0.35,
            "spans": sum(len(v) for v in candidate.values()),
        },
        "baseline": {
            "backend": "ml",
            "mode": "zero_shot",
            "spans": sum(len(v) for v in baseline.values()),
        },
        "gold_docs": len(gold),
        "gold_spans": sum(len(doc.spans) for doc in gold),
        "seed": args.seed,
        "resamples": args.resamples,
        "scores": {
            "candidate_micro": dataclasses.asdict(cand_report.micro),
            "baseline_micro": dataclasses.asdict(base_report.micro),
            "candidate_per_type": {
                k: dataclasses.asdict(v) for k, v in cand_report.per_type.items()
            },
            "baseline_per_type": {
                k: dataclasses.asdict(v) for k, v in base_report.per_type.items()
            },
        },
        "bootstrap": dataclasses.asdict(bootstrap),
        "gate": {
            "promoted": gate.promoted,
            "reasons": list(gate.reasons),
        },
        "runtime_seconds": seconds,
    }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    def _f1(side: str, micro: Scored) -> str:
        return f"{side}: P={micro.precision:.3f} R={micro.recall:.3f} F1={micro.f1:.4f}"

    print(f"docs: {len(gold)}  gold spans: {report['gold_spans']}  runtime: {seconds}s")
    print(_f1("candidate(gliner)", cand_report.micro))
    print(_f1("baseline(ml)     ", base_report.micro))
    print(
        f"bootstrap delta={bootstrap.delta_f1:.4f} "
        f"CI95=[{bootstrap.ci_low:.4f}, {bootstrap.ci_high:.4f}] "
        f"(seed={args.seed}, resamples={args.resamples})"
    )
    print(f"gate: {'PROMOTE' if gate.promoted else 'DO NOT PROMOTE'}")
    for reason in gate.reasons:
        print(f"  {reason}")
    print(f"report: {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="stage", required=True)

    extract = sub.add_parser("extract", help="Run one system over the gold set")
    extract.add_argument("--system", choices=["candidate", "baseline"], required=True)
    extract.add_argument("--gold", required=True, help="Gold JSONL path (stays local)")
    extract.add_argument("--predictions", required=True, help="Per-span JSONL output (stays local)")
    extract.add_argument("--limit", type=int, default=0, help="First N docs only (0 = all)")

    score = sub.add_parser("score", help="Score two prediction files and apply the gate")
    score.add_argument("--gold", required=True, help="Gold JSONL path (stays local)")
    score.add_argument("--candidate-predictions", required=True)
    score.add_argument("--baseline-predictions", required=True)
    score.add_argument("--out", required=True, help="Sanitized report JSON path")
    score.add_argument("--seed", type=int, default=42)
    score.add_argument("--resamples", type=int, default=10_000)

    args = parser.parse_args()
    if args.stage == "extract":
        return _run_extract(args)
    return _run_score(args)


if __name__ == "__main__":
    sys.exit(main())

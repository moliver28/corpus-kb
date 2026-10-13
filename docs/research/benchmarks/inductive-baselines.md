# Inductive-coding recovery baselines (GATOS / TnT-LLM / LLooM) — U29 / issue #79

Committed 2026-10-12, Windows x64, Python 3.11.5 — **local-machine baseline on a
synthetic fixture corpus; absolute numbers are not deployment expectations.**
Everything below is offline and deterministic: no LLM calls, no network, no
database, no Ollama.

> **Recovery-baseline caveat (carried verbatim from the issue):** GATOS-style
> validation uses synthetic planted themes, so results are recovery baselines,
> NOT proof of validity on real interviews.

These scripts measure how well each baseline's inductive workflow *recovers*
known structure that was planted into a synthetic corpus
(`tests/fixtures/inductive_baselines/`: 16 short synthetic interviews — 4
deployment-themed, 4 pricing-themed, 4 performance-themed, 3 mixed, 1 neutral —
with per-theme keyword lists and per-document labels in
`planted_themes.json`). They say nothing about coding quality on real
interview material.

## Shared infrastructure

`scripts/_inductive_common.py` — stdlib-only corpus loading, tokenization,
TF-IDF, greedy average-linkage agglomerative clustering, distinctive-keyword
ranking, pair-counting adjusted Rand index (ARI), and the report scaffolding
(JSON/markdown, `insufficient_data` handling, local-machine label, caveat).

## The three baselines

| Script | Pipeline shape | Would-be-LLM stage |
|---|---|---|
| `scripts/bench_gatos_baseline.py` | Greedy agglomerative clustering, then a topic-model-style pass naming each cluster with distinctive keywords ("codes") | None — fully deterministic; `--seed` is accepted for interface parity and recorded in the report |
| `scripts/bench_tntllm_baseline.py` | Taxonomy-generation-then-classification: induce a taxonomy from a seeded sample, nearest-centroid-classify the rest | Pluggable `TaxonomyInducer` callable; CLI default is a deterministic clustering/keyword heuristic |
| `scripts/bench_lloom_baseline.py` | Concept-induction loop: sample uncovered units, induce concept hypotheses, apply, score coverage, repeat until plateau | Pluggable `ConceptHypothesizer` callable; CLI default is a deterministic distinctive-keyword heuristic |

The TnT-LLM and LLooM pipelines use an LLM at the taxonomy/hypothesis stage in
the published methods. **The scripts here never call an LLM**: the would-be-LLM
stage is a pluggable callable whose default is a deterministic heuristic, and
every report states `llm_stage.used: false`. Supply an LLM-backed callable
programmatically if you want the real methods, and say so in any write-up.

## How to run

From the repo root (any machine with Python 3.11+; no services required):

```bash
python scripts/bench_gatos_baseline.py                    # JSON to stdout
python scripts/bench_tntllm_baseline.py --format markdown # human-readable
python scripts/bench_lloom_baseline.py --seed 20261012    # fixed default seed

python scripts/bench_gatos_baseline.py --help             # carries the caveat verbatim
```

Common flags (all three): `--input DIR` (default
`tests/fixtures/inductive_baselines/`), `--seed` (fixed default `20261012`),
`--format json|markdown`. GATOS adds `--threshold` / `--max-keywords`; TnT-LLM
adds `--sample-frac`; LLooM adds `--max-iterations` / `--sample-size`. Every
run exits 0 whether it produced numbers or an honest `insufficient_data`
report.

## Observed results (this machine, default flags)

### GATOS-style (`bench_gatos_baseline.py`)

3 clusters induced at `--threshold 0.05`; **ARI vs planted themes 0.543**.
Per-theme recovery (best cluster): deployment F1 0.889 / keyword-Jaccard 0.364;
pricing F1 0.800 / 0.500; performance F1 0.889 / 0.500. Recall 1.0 for all
three planted themes (each gets a dedicated cluster); precision 0.667–0.8
because the three mixed interviews attach to theme clusters.

Post-hoc threshold sweep (0.01–0.30, step 0.01) peaks at ARI 0.543 for
thresholds 0.04–0.05 (3 clusters); above ~0.06 the corpus shatters into
singletons (ARI 0.0 from 0.15 up). **The sweep best is an optimistic oracle
bound — the threshold is chosen after seeing the labels; the primary
`--threshold` run is the honest operating point.** The script labels this
explicitly (`oracle_note`) and the primary run happens to sit at the optimum.

### TnT-LLM-style (`bench_tntllm_baseline.py`)

Seeded 8/8 sample/held-out split (`--seed 20261012`); heuristic taxonomy
induction finds 3 codes (sizes 4/3/1 in the sample). **ARI vs planted themes
0.265; held-out pure-theme accuracy 0.5** (4 held-out docs carry a pure planted
theme label; a hit means the doc was assigned the code whose sample members
are majority-planted with the doc's theme). With an 8-document sample the
taxonomy stage is already sample-sensitive (one singleton code); this is the
expected behavior of the heuristic stand-in, not TnT-LLM itself.

### LLooM-style (`bench_lloom_baseline.py`)

Loop stops on `coverage_plateau` after 6 productive iterations (cap 8) with
**6 concepts and final coverage 1.0**. **ARI vs planted themes 0.16** (docs
coded to their first covering concept; uncovered docs would count as their own
group). Per-theme best-concept term-Jaccard: pricing 0.091, performance 0.091,
deployment 0.0 (no induced concept matched the deployment keyword set — the
2-term keyword heuristic latches onto surface phrases like `staging+document`
before theme words). Concept-induction coverage is easy; concept *quality*
under a term-matching heuristic is the weak point, and the numbers say so
honestly.

## `insufficient_data` convention

Below `MIN_DOCS = 8` documents every script emits (JSON shape, identical
fields in markdown) and exits 0:

```json
{
  "status": "insufficient_data",
  "reason": "fixture corpus has 4 documents; at least 8 are needed for a meaningful inductive-coding run, so no numbers are reported",
  "corpus_status": "insufficient_data",
  "document_count": 4,
  "label": "local-machine baseline on a synthetic fixture corpus; absolute numbers are not deployment expectations",
  "recovery_baseline_caveat": "GATOS's validation uses synthetic planted themes, so results are recovery baselines, not proof of validity on real interviews."
}
```

No recovery numbers, clusters, concepts, or taxonomy entries are reported when
the corpus is too small — the scripts never fabricate statistics.

## Smoke tests

`tests/test_inductive_baselines.py` runs each script's `main` on the fixture
corpus (meaningful path), on a 2-document temp corpus (`insufficient_data`
path), and in markdown mode; checks the caveat reaches `--help` verbatim;
exercises the pluggable `TaxonomyInducer` / `ConceptHypothesizer` seams; and
asserts run-to-run determinism. 17 tests, ~0.3 s total, fully offline.

## Scope limits

- Fixtures are 250–400-word synthetic interviews written to make planted
  themes lexically recoverable. Real interviews are messier (sarcasm, overlap,
  topic drift); none of that is represented.
- ARI and per-theme precision/recall compare *partitions*; the LLooM loop
  assigns each doc at most one concept, so multi-theme (mixed) docs are scored
  against a single label and drag ARI down by construction.
- Keyword-Jaccard depends on the planted keyword vocabulary in
  `planted_themes.json`; induced codes using synonyms the fixture author did
  not list are scored as misses.
- Results are recorded once, on one machine (see header). Rerun locally with
  the commands above; the runs are deterministic, so numbers should reproduce
  exactly on the same fixture set and code revision.

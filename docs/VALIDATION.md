# Validation: calibrate then lock

Corpus-KB can find the query-time recipe that ranks your corpus best.
A `calibrate:` block in a profile names the recipe family, the sample,
and the golden labels. One sweep scores every member against the same
ingested corpus. A paired test with a Holm correction guards the pick.
The winner lands in `<profile>.lock.json` next to the profile.

This page walks one full loop. Follow the six steps in order.

## What a sweep measures

Every candidate ranks the same stored corpus. Only the query-time
settings change: the fusion constant, the per-arm candidate depths, and
the reranker switch. The sweep changes no stored data. A win applies at
query time, so you never re-ingest.

## Step 1 — Pick a golden set and a sample

A golden set is a JSONL file of labeled questions. The format and the
label kinds live in [EVALUATION.md](EVALUATION.md). For the sweep, use
span or unit labels. Chunk-id labels also load, but chunk ids change
when a chunker changes, so they compare less fairly.

The sample is one transcript file, or a folder of them. Put the sample
and the golden file inside the profiles directory (`profiles/` by
default). Aim for at least 30 labeled questions that hit the sample.
Fewer scored questions than `min_queries` stops the sweep.

Check the file before you sweep:

```bash
corpus-kb eval build-golden --validate golden.jsonl
```

## Step 2 — Author the candidates

Add a `calibrate:` block to your profile YAML:

```yaml
calibrate:
  candidates:
    - default                            # member 1: the untouched config
    - transcript: {retrieval.rrf_k: 10}  # member 2: tighter fusion
  baseline: default
  sample: calib-sample/
  golden: calib-golden.jsonl
  k: 10
  min_overlap: 0.5
  alpha: 0.05
  min_queries: 30
```

A candidate is a name, or a one-key mapping that adds recipe overrides.
A plain name ranks with the untouched config. The fields:

| Key | Meaning | Default |
|-----|---------|---------|
| `candidates` | 1 to 8 family members. Two members cannot share a name. | required |
| `sample` | One transcript file or a folder, under the profiles directory. | required |
| `golden` | A JSONL golden file, under the profiles directory. | required |
| `baseline` | The member every challenger must beat. It joins the family on its own. | first candidate |
| `k` | Hits per ranked query. | `10` |
| `min_overlap` | Span-label overlap that marks a chunk relevant. | `0.5` |
| `alpha` | The significance bar for the paired test. | `0.05` |
| `min_queries` | The least scored queries per member. | `30` |

The override keys, and nothing else:

| Key | Config block | What it changes |
|-----|--------------|-----------------|
| `retrieval.rrf_k` | `retrieval:` | the RRF fusion constant |
| `retrieval.vector_candidates` | `retrieval:` | vector arm depth before fusion |
| `retrieval.lexical_candidates` | `retrieval:` | lexical arm depth before fusion |
| `reranking.enabled` | `reranking:` | the reranker on or off |
| `reranking.over_retrieve_n` | `reranking:` | candidates sent to the reranker |
| `reranking.score_floor` | `reranking:` | drop results below this score |

The gates are strict, on purpose:

- More than 8 candidates fails the profile load.
- An unknown recipe key or calibrate key fails the profile load.
- A candidate name must be a registered strategy. Run
  `poe validate-profiles` to check names and paths before you sweep.

## Step 3 — Run the sweep

Three edits set the sweep up:

1. Add `validate: "calibrate"` to the profile. Quote the value; YAML
   reads a bare `calibrate` fine, but a bare `off` becomes false.
2. Point `ingestion.profile` at the profile in `config.yaml`.
3. Ingest through the standard front door:

```bash
corpus-kb ingest interviews/
```

The sweep runs before the ledger opens. One scratch tenant serves the
whole family. The sample is ingested once. Each member then ranks the
golden queries through its own overrides. The tenant is deleted
afterwards. Only the lock file survives.

Two needs: the Postgres backend (DuckDB raises a typed error), and an
up embedder. A zero-vector fallback refuses to score, so fake numbers
never reach the lock.

## Step 4 — Read the outcome

The ingest result records the sweep summary. Three outcomes exist:

| Outcome | Meaning | What the sweep wrote |
|---------|---------|----------------------|
| `success` | One challenger beat the baseline past the corrected bar. | The lock file, with the winner. |
| `no_significant_winner` | The family ran. Nothing cleared the bar. | Nothing. Your config stays as it is. |
| `insufficient_data` | The numbers would be fake or thin. The reason names the fix. | Nothing. |

These reasons produce `insufficient_data`:

- The embedder was down, so the zero-vector fallback ran.
- No golden label matched any ingested chunk.
- A member scored fewer than `min_queries` queries.
- The `calibrate` extra is missing, so the sign test ran and no
  p-value exists. Install it and retry.

The winner is the significant challenger with the highest nDCG@10.
Ties break on recall@k, then on the candidate name.

## Step 5 — Apply a win

On `success`, the lock carries `proposed_overrides`. Copy each key into
`config.yaml`, under its block. Say the lock holds:

```json
"proposed_overrides": {"retrieval.rrf_k": 10}
```

Then `config.yaml` gains:

```yaml
retrieval:
  rrf_k: 10
```

The new values take effect at the next query. No re-ingest happens,
because the change touches rank time only.

A reranker win sets `reranking.enabled: true`. Check the reranker with
`corpus-kb doctor`. It reports embedding dims and reranker state. It
also sends a sanity pair to a llamacpp reranker. A llama-server on that
path needs `--reranking --pooling rank --embedding`.

A CLI apply command is planned in issue #200. Until it ships, the copy
is manual.

## Step 6 — Keep the lock current

The lock records what the sweep proved:

- The profile and golden SHA-256 hashes, plus the sample manifest.
- The overlap rule, `k`, and `min_overlap`.
- Every member's mean metrics.
- `chosen`, `baseline`, `alpha`, `min_queries`, and `outcome`.
- `proposed_overrides` and `reranker_active`.
- The embedding model and dims, the tokenizer, and the versions.

`enforce` mode compares the profile file hash to the lock. An edit
after calibration fails the next enforce ingest with both hashes. Re-run
the sweep to accept the edit, or revert it.

Two sweeps on the same inputs write the same lock, except the date.

## Iterate

Change one knob at a time. Grow the golden set as you learn the corpus.
Re-run the sweep; the new lock replaces the old one. Stop when a sweep
answers `no_significant_winner` twice — your settings are then as good
as your labels can prove.

## Where to read more

- [EVALUATION.md](EVALUATION.md) — the golden format, the metrics, and `eval run`.
- [HARNESS-PLAYBOOKS.md](HARNESS-PLAYBOOKS.md) — profile fields and the starter profiles.
- [ingestion-recipes-as-built.md](research/ingestion-recipes-as-built.md) — the design and the papers behind the tests.

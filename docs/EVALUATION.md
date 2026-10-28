# Offline evaluation

The `eval` command family scores search and answer quality on your own
machine. It runs fully local: your database, your Ollama models, no
cloud calls. The optional `deepeval` package powers the answer metrics;
everything else needs no extra.

## The golden format

A golden set is a JSONL file. Each line is one labeled question:

```jsonl
{"query": "startup order", "relevant_chunk_ids": ["0b6e...-chunk-uuid"], "answer": null}
{"query": "retry policy", "relevant_chunk_ids": ["31a2...-chunk-uuid"], "answer": "Retry three times."}
{"query": "rate limits", "relevant_chunk_ids": ["9c4d...-chunk-uuid", "71f0...-chunk-uuid"], "answer": null}
```

Each line carries three keys. `query` is the question text.
`relevant_chunk_ids` lists the chunk ids a good search must return.
`answer` is an optional reference answer; answer metrics score records
that have one and skip records that do not.

Get chunk ids from `corpus-kb search "question" --json` output. Copy the
`chunk_id` values of the hits you judge relevant.

## Span and unit labels

Chunk ids change when a chunker changes, so chunk-id labels cannot
compare chunkers fairly. Two more label kinds solve this. A line
carries exactly one kind:

```jsonl
{"query": "grid plan", "relevant_spans": [{"doc": "sym://a", "start": 0, "end": 120}]}
{"query": "grid plan", "relevant_units": [3, 17]}
```

A span names a document plus a char range. The `doc` value is the
document source label. A chunk is relevant when the label covers at
least `min_overlap` of it (default 0.5). A unit label names a research
unit id directly.

The calibration sweep uses these labels; the full author, measure, and
apply loop is [VALIDATION.md](VALIDATION.md).
`eval build-golden --validate` accepts all three label kinds.

## Build a golden set from reviewed codings

Coding corpora get labels from human review, never from raw model
output. `--from-assignments` reads your confirmed and overridden coding
assignments and turns each code into one golden record:

```bash
corpus-kb eval build-golden --from-assignments \
  --project 00000000-0000-0000-0000-000000000002 --out golden.jsonl
```

The query text is the code name plus its brief definition. The relevant
chunk ids are the deterministic retrieval-chunk ids of the reviewed
units. Records without reviewed units are left out.

Check an existing file without touching the database:

```bash
corpus-kb eval build-golden --validate golden.jsonl
```

Validation is strict. A malformed line names its line number and the
problem.

## Run the evaluation

```bash
corpus-kb eval run --golden golden.jsonl --k 10
```

The command prints mean recall@k, MRR, and nDCG@10, plus per-record
rows. `--json` emits the full envelope for scripts. `--report out.json`
writes the same envelope to a file. Use `--skip-answer-metrics` for a
retrieval-only run. Use `--project` to scope the search to one research
project. Assignment-built goldens need the project id you reviewed under.

Exit codes: 0 means the run answered, including an empty golden set.
1 means bad input or failure. 3 means the database is unreachable.

## Metric glossary

- recall@k: the share of relevant chunk ids that appear in the top k hits.
- MRR: the reciprocal rank of the first relevant hit, averaged over records.
- nDCG@10: the ranking quality of the top ten, rewarding relevant hits placed higher.
- faithfulness: the share of answer claims the retrieved evidence supports.
- answer relevancy: how closely the produced answer addresses the question.
- contextual recall: the share of the reference answer found in the retrieved evidence.
- contextual precision: how high the useful evidence ranks inside the retrieved context.

The retrieval metrics reuse the research benchmark code, so eval numbers
match the numbers in docs/retrieval-benchmarks.md.

## The local judge

Answer metrics run a local Ollama model as the judge, at temperature 0.
The judge model defaults to your configured answer model (`llm.model`).
No API key exists on this path, and no request leaves your machine.

## Honest skips

The answer section reports `skipped` with a reason instead of failing:

- `--skip-answer-metrics` was set.
- The `deepeval` package is missing. The reason is the fix:
  `pip install 'corpus-kb[eval]'`.
- No golden record carries a reference answer.

A metric that fails on one record scores None for that record. No score
is ever invented.

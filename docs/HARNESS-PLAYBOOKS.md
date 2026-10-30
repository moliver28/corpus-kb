# Harness playbooks

This doc is the operating script for the AI harness. End users are not
experts; the harness is. The harness reads the sample data first. Then
it asks at most six questions. It drafts a profile, validates it, and
runs the paved pipeline. No user code anywhere.

## The one decision flow

1. Sample the corpus. Read three to five documents.
2. Fill the decision table below. Never ask the user table questions.
3. Ask the user at most six plain questions (see the wizard list).
4. Write `profiles/<project>.yaml` in the repo's profiles directory.
5. Run `poe validate-profiles` (or `corpus-kb` setup) to gate it.
6. Set `ingestion.profile: <project>` in `config.yaml`.
7. Ingest the corpus. Read `profile` and `strategy_basis` in results.
8. After review decisions land, run `extraction-tune` to write seeds.

## Decision table

| Data type | Ingestion strategy | Extraction mode | Templates to list |
| --- | --- | --- | --- |
| Interview transcripts | `transcript` | `few_shot` or `customized` | `preference`, `opinion`, `sentiment_target`, `temporal_change` |
| Focus groups | `transcript` | `few_shot` | `preference`, `opinion`, `sentiment_target` |
| Surveys | `text` or `default` | `zero_shot` | `sentiment_target`, `numeric_claim` |
| News or reports | `markdown` or `default` | `zero_shot` | `fact_triple`, `attribution`, `numeric_claim` |
| Mixed corpora | detection-driven | `zero_shot` first, tune later | `preference`, `opinion`, `fact_triple` |

Rules of thumb:

- Start at `zero_shot`. It needs no examples and no tuning.
- Move to `few_shot` when answers feel generic.
- Move to `customized` only after `extraction-tune` has written seeds.
- One interview session is one transcript document. Keep it that way.
- List only templates the project needs. Fewer templates, cleaner atoms.

## The wizard: six questions or fewer

Ask only what the sample data cannot answer. Skip any question the
sample answers. Each question offers a default, so the user can
answer "yes" and move on.

1. Who speaks in this corpus? (interviewees only, or hosts too?)
2. Should one file be one document, or one speaker turn one document?
3. What decisions should the corpus track? (likes, choices, ratings?)
4. Which names or projects matter that the ontology may not know?
5. Do old opinions matter, or only the current belief? (drives
   `statements-timeline` and `beliefs-timeline` use, not config.)
6. What should the corpus flag for your review? (conflicts only, or
   low-confidence statements too?)

Then draft, validate, and apply. Show the drafted profile to the user
before the first full ingest.

## The profile file

Profiles live in `profiles/` next to `config.yaml`. One file per
project. The file name is the profile name. This is the shipped
starter:

```yaml
name: starter-interview
description: Paved starting profile for interview corpora.
data_type: interview
ingestion:
  default: transcript
extraction:
  mode: few_shot
templates:
  - preference
  - opinion
```

Field reference:

- `name`: must match the file name without the `.yaml` suffix.
- `data_type`: `interview`, `focus_group`, `survey`, `news`, `mixed`,
  `product`, `engineering`, or `design`. There is no `research` type.
  Research transcripts use `interview` or `focus_group`. The research
  ingest path reads those turn by turn.
- `ingestion.strategies`: maps a source type to a strategy name.
- `ingestion.default`: the strategy when nothing else decides.
- `extraction.mode`: `zero_shot`, `few_shot`, or `customized`.
- `validate`: `off`, `advisory`, `enforce`, or `calibrate`. Quote the
  value. YAML reads a bare `off` as false. This is the default gate for
  callers that pass no `validate` argument. A caller-passed mode wins.
- `templates`: claim-template ids from `config/claim_templates/`.
- `seeds_file`: a seeds file in the profiles directory (the
  `extraction-tune` op writes and points to it for you).
- `calibrate`: an optional sweep block. See "Calibrate then lock".

Precedence is fixed. An explicit per-call strategy override wins. The
profile wins next. The `ingestion.strategies` config map wins next.
Content detection wins next. The default wins last. The result reports
which layer decided in `strategy_basis`.

## Starter profiles

Four starters ship. Copy one, rename it, and adjust it. Each starter
sets `validate: advisory`, so findings ride the ingest result and never
block a write.

| Profile | Data type | Ingestion defaults |
|---------|-----------|--------------------|
| `starter-interview` | `interview` | transcript strategy for interview sessions |
| `starter-product` | `product` | surveys, web pages, markdown files |
| `starter-engineering` | `engineering` | code files, markdown files |
| `starter-design` | `design` | slide decks, web pages, markdown files |

The `code` strategy needs the optional `code` extra. Without it, code
files still ingest. The strategy falls back to the default plan and
records a warning.

## Calibrate then lock

A profile can name a calibration sweep. Add a `calibrate:` block:

```yaml
calibrate:
  candidates: [default, markdown_sections]
  sample: calib-sample/
  golden: calib-golden.jsonl
  k: 10
  min_overlap: 0.5
```

The paths resolve against the profiles directory. `sample` is one
transcript file or a folder of them. `golden` is a JSONL file with
span or unit labels. `poe validate-profiles` checks that every path
exists and every candidate is a registered strategy.

The sweep runs when an ingest uses `validate: calibrate` with that
profile. The sweep ingests the sample once into one scratch Postgres
tenant. Every candidate then ranks the same corpus through its own
overrides. The sweep needs the Postgres backend; DuckDB raises a typed
error. It scores the golden queries and writes `<profile>.lock.json`
next to the profile. Only the lock file survives the sweep. Two sweeps
on the same inputs write the same lock, except the date. The full
walk-through is [VALIDATION.md](VALIDATION.md).

The sweep refuses to score when embeddings are degraded. Fake vectors
would give fake numbers, so it answers `insufficient_data` instead.
The optional `calibrate` extra adds paired statistics through ranx.
Without the extra, reports use a sign test and say so.

`enforce` mode compares the profile file to the lock. An edit after
calibration fails with both hashes, so a locked profile cannot drift
silently. Re-run the sweep to accept the edit.

## Tuning with the tune op

Run it after review decisions accumulate. It mines confirmed evidence
into artifacts.

```bash
corpus-kb extraction-tune --profile my-project --dry-run
corpus-kb extraction-tune --profile my-project
```

- `method=exemplar` (default) mines resolved review statements and
  high-confidence relations into `<profile>.seeds.yaml`, then sets the
  profile's `seeds_file`. Ingest again with `extraction.mode:
  customized` and the seeds teach the extractor.
- `method=lora` trains a GLiNER LoRA adapter on confirmed entity
  spans. It needs the `tune` extra. The adapter is a candidate only;
  the eval promotion gate decides promotion.

Both methods accept `--dry-run`: same envelope, zero writes.

## Reading results

- Ingest results carry `profile` (the active profile name) and
  `strategy_basis` (which layer decided the strategy).
- `statements-list` and `statements-timeline` show the atoms.
- `conflicts-scan` flags contradictions; `conflicts-resolve` records
  the human verdict; resolved verdicts feed the next tune run.
- `beliefs-current`, `beliefs-timeline`, `beliefs-predict`, and
  `beliefs-compare` read the Bayesian layer. Every belief response
  carries a credible interval.

## Coarse routing: the meta layer

`corpus-kb analysis-embed-meta` builds one aggregate embedding per
document, topic, belief, and the active profile. Every search response
then carries `meta_hits`: the closest aggregates with their distances.
Run the op after ingests and after `conflicts-resolve` decisions. The
search stays chunk-level; `meta_hits` only routes.

## Recipe: queue the textbooks

Textbooks are long jobs. The ingest queue keeps them out of the
interactive path. Enqueue lands rows, and a worker drains them one at a
time. Every step below has an MCP tool and an HTTP route over the same
three ops (`queue_enqueue`, `queue_status`, `queue_cancel`). The CLI is
shown because it reads best.

1. Find the candidates first. The scan prints one scored line per PDF:

   ```bash
   corpus-kb textbook scan /data/books
   ```

2. Enqueue each candidate with the project profile. Enqueue is
   idempotent: a repeat of the same file, profile, and strategy config
   answers the existing row with `deduplicated: true`.

   ```bash
   corpus-kb queue-enqueue /data/books/networks.pdf \
       --title "A History of Networks" --profile my-project
   ```

   `--strategy-config '{"window_pages": 25}'` rides along when a book
   needs different windowing; different config never dedupes into an
   older row.

3. Run the worker. `--watch` keeps polling every `queue.poll_seconds`
   until you press Ctrl-C. The first Ctrl-C finishes the current book
   and prints the end-of-run report; a second Ctrl-C stops at once.

   ```bash
   corpus-kb queue-run --watch
   ```

4. Watch status from another shell (or from the SPA's Ingest page,
   Queue tab). Rows report status, attempts, elapsed time, and — once a
   worker claims them — `pages_done`, `pages_total`, and `chunks`.

   ```bash
   corpus-kb queue-status
   ```

5. Stop a pending or running row by id. A done, failed, or cancelled
   row answers a conflict instead.

   ```bash
   corpus-kb queue-cancel <queue_id>
   ```

**The autorun flag.** By default (`queue.autorun: false`) the queue
stays at rest until a worker starts. Set `queue.autorun: true` in
`config.yaml` to let the server start a worker at boot. Keep it false
when you want enqueue and drain on your own schedule. The
`queue.max_attempts` cap still bounds every retry either way.

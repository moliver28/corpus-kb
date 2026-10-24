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
- `data_type`: `interview`, `focus_group`, `survey`, `news`, `mixed`.
- `ingestion.strategies`: maps a source type to a strategy name.
- `ingestion.default`: the strategy when nothing else decides.
- `extraction.mode`: `zero_shot`, `few_shot`, or `customized`.
- `templates`: claim-template ids from `config/claim_templates/`.
- `seeds_file`: a seeds file in the profiles directory (the
  `extraction-tune` op writes and points to it for you).

Precedence is fixed. An explicit per-call strategy override wins. The
profile wins next. The `ingestion.strategies` config map wins next.
Content detection wins next. The default wins last. The result reports
which layer decided in `strategy_basis`.

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

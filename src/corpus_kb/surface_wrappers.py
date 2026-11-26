"""Harness wrapper bodies for the surface registry (split module).

The Wrapper row type, the per-command guidance texts, and the WRAPPERS
table moved here verbatim from surface_registry.py (W4-1). The
registry loads this file by path and re-exports Wrapper and
WRAPPERS. Consumers keep importing them from the registry.

STRICTLY STDLIB-ONLY: zero corpus_kb or third-party imports (r9 --
the agent-config-consistency CI job installs NO dependencies, so any
import here is a ModuleNotFoundError the local bar cannot catch).
Loaders read this file by path, not as a package member.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path as _Path
from typing import NamedTuple

# --------------------------------------------------------------------------
# Harness wrappers: the generated command pack
# --------------------------------------------------------------------------


class Wrapper(NamedTuple):
    """One generated harness command (emitted to Claude Code AND OpenCode)."""

    name: str
    title: str
    description: str
    argument_hint: str
    body: str  # invocation guidance + `corpus-kb <cmd> --help` pointers ONLY
    covers: tuple[str, ...] = ()  # Surface.name values this wrapper fronts
    planned: bool = False  # planned rows are documented but NOT emitted


_INGEST_BODY = """# corpus-ingest — ingest research transcripts

Load interview / focus-group transcripts into the research knowledge base.

- Single file (auto-detected txt / vtt / srt / csv / docx):

  ```
  corpus-kb research ingest-transcript <path> --project-id <uuid>
  ```

- File, directory, or glob with file-hash + text-hash dedup; add `--watch`
  to tail a drop directory:

  ```
  corpus-kb research ingest <path> [--watch] [--force]
  ```

- All flags: `corpus-kb research ingest --help`
- Re-runs only process new or changed files; do not delete the ingest ledger.
"""

_CODING_RUN_BODY = """# corpus-coding-run — deductive coding run

Score codable units against a codebook version (three-view scoring with
calibrated thresholds and conformal routing).

```
corpus-kb coding run --codebook-version-id <uuid> [--project-id <uuid>] [--alpha 0.1]
```

- The summary JSON reports explicit / question-dependent / review splits.
- Assignments land after the projection catch-up (run for you).
- All flags: `corpus-kb coding run --help`
"""

_PROMOTE_BODY = """# corpus-codebook-promote — promote a proposed code (HUMAN GATE)

Promotion is never automated: review the proposal, then mint the next
codebook version yourself.

```
corpus-kb codebook promote --proposed-id <int> --name "<code name>" \\
  --definition "<definition>" [--inclusion ...] [--exclusion ...]
```

- A duplicate-gate block returns exit code 3 with a merge suggestion.
- All flags: `corpus-kb codebook promote --help`
"""

_REPORT_BODY = """# corpus-research-report — governance report

Emit the ONE governance artifact: exhaustiveness (R(tau_res), coverage
curve), cluster stability, semantic overlap, IRR (alpha + AC1), keywords,
G3 audit, and conformal set sizes.

```
corpus-kb research report [--level novice|expert] [--json]
```

- Novice level prints traffic lights + plain language + next actions.
- All flags: `corpus-kb research report --help`
"""

_REVIEW_BODY = """# corpus-review — execute review decisions

Record one human decision per CodingAssignment; the event lands through the
projection so weights and audits see it.

- Confirm the model's assignment:

  ```
  corpus-kb review accept <assignment-id> --reviewer <you> [--note ...]
  ```

- Overrule it (link corrections describe the change):

  ```
  corpus-kb review override <assignment-id> --reviewer <you> --note "<fix>"
  ```

- All flags: `corpus-kb review accept --help`
"""

_DEMO_BODY = """# corpus-demo — narrated demo pipeline

Run the full qualitative-coding pipeline on the bundled two-interview demo
corpus (docs/demo-corpus/): ingest, codebook seed, coding run, review gate,
governance report, and a cited notebook answer.

```
corpus-kb research demo
```

- Requires `corpus-kb setup` (Postgres + migrations + models) plus a
  research-grade embedder: pull `qwen3-embedding:8b-q8_0` and set
  `embedding.model`/`dimensions` accordingly (>=1024 dims - the default
  nomic-embed-text makes the demo abstain by design; see
  docs/GETTING-STARTED.md "Before you start").
- Every stage narrates what it is doing and links the relevant docs.
- Re-runs are safe: file and text hashes make ingested files no-ops.
- Long-running and synchronous on every surface: http/mcp/socket POSTs
  return a receipt envelope (status, exit code, captured narration).
- All flags: `corpus-kb research demo --help`
"""

_RELEASE_BODY = """# corpus-release — the release lifecycle (v6 §6)

Walk a codebook version from draft to released codebook: assess the gates,
open the draft, review the packet, execute with a named approver, and diff
successors.

- Compose EVERY gate over current evidence (writes NOTHING; not_evaluable
  gates are reported, never silently passed):

  ```
  corpus-kb release assess --codebook-version-id <uuid> [--profile <name>]
  ```

- Open a draft_candidate release pinned to the current content hash:

  ```
  corpus-kb release request --codebook-version-id <uuid> --creator <you>
  ```

- The review packet: gate evidence, failing/not_evaluable gates, waivers,
  and the exact next commands:

  ```
  corpus-kb release review --codebook-version-id <uuid> --release-id <release-id>
  ```

- Execute the release (HUMAN APPROVER required; enforcement=enforce blocks
  unmet required gates, `--waive GATE=justification` records explicit
  waivers; a refusal exits 1 with a structured error):

  ```
  corpus-kb release release --codebook-version-id <uuid> --release-id <release-id> --approver <you>
  ```

- Read the trail; every command takes --json:

  ```
  corpus-kb release status --codebook-version-id <uuid>
  corpus-kb release compare --codebook-version-id <uuid> --old-release-id <a> --new-release-id <b>
  ```

- All flags: `corpus-kb release status --help` (and each subcommand)
"""

_CYCLE_BODY = """# corpus-research-cycle — the full pipeline with human-critical gates

Chain ingest -> inductive -> deductive -> keywords -> report without
writing a step script. The cycle HALTS at human-critical gates -
`codebook_promotion` is hard-floored (promotion is never automated) -
prints `AWAITING HUMAN: <gate>` plus the exact next command, and resumes
when you re-run it after acting on the gate.

Postures:

- Fully in the loop - exactly ONE stage per call:

  ```
  corpus-kb research cycle --mode in
  ```

- On the loop - one stage, then it asks before continuing:

  ```
  corpus-kb research cycle --mode on [--guide]
  ```

- Out of the loop - unattended except at the halt gates:

  ```
  corpus-kb research cycle --mode out --dir <drop-dir> --question "<q>" [--guide]
  ```

- Watch a drop directory in the foreground (re-arms on new files):

  ```
  corpus-kb research cycle --mode out --watch <dir>
  ```

- `--guide` turns every halt into a taught decision (proposed codes,
  example units, promote-vs-skip consequences); `--json` emits one
  stage/gate event per line for agents.
- Unattended runs: `--approve` confirms every gate without asking;
  `--deny` refuses every gate. Closed stdin cannot hang the cycle.
- Long-running and synchronous on every surface: http/mcp/socket POSTs
  run the same cycle and return the receipt when it ends.
  Exit codes: 0 complete; 2 embeddings unavailable; 10-17 one gate
  halted; 20 approval denied; 21 awaiting approval.
- All flags: `corpus-kb research cycle --help`
"""

_FRONT_DOOR_BODY = """# corpus-ingest-front-door — ingest files, watch, status, reembed

The top-level ingest front door: file/directory ingest with honest exit
codes, a watched drop folder that auto-heals embedding outages, and the
run-ledger surfaces.

- Ingest one file or a whole directory:

  ```
  corpus-kb ingest <path> [--source-type transcript]
  ```

- Watch a directory; runs that failed ONLY because embeddings were
  unavailable are re-attempted automatically once the provider is back
  (health probe + backoff + capped attempts):

  ```
  corpus-kb ingest <dir> --watch [--no-heal]
  ```

- Exit codes (stable for scripting, printed in --help): 0 ok, 1 partial,
  2 failed, 3 config.

- Status and re-embed surfaces (all flags via --help):

  ```
  corpus-kb ingest-status
  corpus-kb ingest-reembed [--limit N] [--doc-id <uuid>]
  ```

- After an embedding-model change, re-embed every stored chunk in one
  batched pass (no re-partition, no re-extract). Dry-run first for the
  size; a re-run skips chunks the target model already wrote:

  ```
  corpus-kb ingest-reingest [--dry-run] [--project-id <uuid>]
  ```
"""

_TOPICS_BODY = """# corpus-topics — the topic layer (Wave 4)

Offline BERTopic over the chunk embeddings ingest already wrote. The
feature is dormant by default: `topics.enabled: false` in config.yaml.

- Build topics (consumes `chunks_vectors.embedding`; never re-embeds):

  ```
  corpus-kb topics-build
  ```

- List the built topics, largest first:

  ```
  corpus-kb topics-list [--limit N]
  ```

- Topic trends over document dates (analysis program; dormant by
  default, needs `analysis.topics.over_time.enabled: true`):

  ```
  corpus-kb topics-trends [--bins N]
  ```

- A disabled feature is an honest no-op result, not an error. A missing
  extra exits 3 with the remedy: `pip install 'corpus-kb[topics]'`.
- All flags: `corpus-kb topics-build --help`
"""

_LABELING_BODY = """# corpus-labeling — the weak-supervision labeling layer

Score user-authored labeling functions over the corpus and aggregate
them into probabilistic chunk labels. Dormant by default:
`analysis.labeling.enabled: false` in config.yaml.

- Build the chunk labels from the registered labeling functions:

  ```
  corpus-kb labeling-build
  ```

- List the built chunk labels and the registered functions:

  ```
  corpus-kb labeling-list [--limit N]
  corpus-kb labeling-lfs [--limit N]
  ```

- Rank weakly supported labels for human review (advisory; never
  relabels):

  ```
  corpus-kb labeling-triage [--cv-folds 5] [--flag-threshold 0.2]
  ```

- Score the built labels against a small gold dev set file (a JSON
  object that maps chunk ids to label names):

  ```
  corpus-kb labeling-gold-check --gold-file <path>
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb labeling-build --help`
"""

_CLASSIFICATION_BODY = """# corpus-classification — few-shot classification over the corpus

Fit a classifier against a seed label set and write calibrated chunk
predictions. Dormant by default: `analysis.classification.enabled:
false` in config.yaml.

- Build the predictions (tier `head` on stored embeddings, or the
  opt-in `setfit` tier):

  ```
  corpus-kb classification-build [--seed-file <path>]
  ```

- List the built predictions (bounded window):

  ```
  corpus-kb classification-list [--limit N]
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb classification-build --help`
"""

_ANALYSIS_BODY = """# corpus-analysis — the composed analysis run

Date provenance plus the composed corpus analysis report. The run is
dormant until you enable the analysis flags it needs.

- Backfill `documents.document_date` from chunk metadata (idempotent;
  needs `analysis.topics.over_time.enabled: true`):

  ```
  corpus-kb analysis-backfill-dates
  ```

- Run the composed analysis (dates, labels, predictions, trends) and
  write the report:

  ```
  corpus-kb analyze-run [--output <path>]
  ```

- A disabled feature is an honest no-op result, not an error.
- All flags: `corpus-kb analyze-run --help`
"""

# -- AMR program (wave 2): the register-backed method cards -----------------
# The card builder and the three wave-4 method cards live in amr_cards.py
# (also stdlib-only). This file loads it by path, like its own consumers
# do, so the zero-import rule here stays true.

_cards_spec = importlib.util.spec_from_file_location(
    "corpus_kb._amr_cards", _Path(__file__).with_name("amr_cards.py")
)
assert _cards_spec is not None and _cards_spec.loader is not None
_cards = importlib.util.module_from_spec(_cards_spec)
_cards_spec.loader.exec_module(_cards)

_amr_body = _cards._amr_body
_SURVEY_CODES_BODY = _cards._SURVEY_CODES_BODY
_SEVERITY_BODY = _cards._SEVERITY_BODY
_EMOTION_BODY = _cards._EMOTION_BODY

_SELECT_BODY = _amr_body(
    "analysis-select-method",
    family="any (you name it)",
    inputs="none (the selector reads the register, not the corpus)",
    calibration="none",
    autonomy="automated_with_audit",
    run='analysis-select-method "<task>" [--output-family <family>] '
    "[--input <kind>] [--max-complexity-tier <tier>]",
    cautions="the rank is registry order, not evidence quality. The rubric "
    "cannot see your question's meaning.",
    validation="read the top rows' validation_required and permitted_claims before you pick one.",
    claims="a ranked shortlist with the register version recorded.",
)
_ROUTE_BODY = """# corpus-analysis-route — question front door

Route one plain-language question to the right analysis method.

- Ask the question; read the method, the one-line why, and the run hint:

  ```
  corpus-kb analysis-route "<question>"
  ```

- An ambiguous answer lists readings. Re-run with your pick:

  ```
  corpus-kb analysis-route "<question>" --method-id <id>
  ```

- The status says resolved, ambiguous, or degraded. Degraded means no
  embedder ran; the phrase rules still answered, and the trace says
  which mode ran.
- The trace records the mode, the threshold, the margin, and the scores.
- Exit codes: 0 answered, 1 failed, 3 unavailable.
- All flags: `corpus-kb analysis-route --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
"""
_KEYPHRASES_BODY = _amr_body(
    "keyphrases-extract",
    family="themes (methods 05, 06, 07)",
    inputs="raw_text (the embedding variant also uses embeddings; the llm variant uses an LLM)",
    calibration="low to moderate",
    autonomy="automated_with_audit",
    run="keyphrases-extract [--variant tfidf|embedding|llm] [--limit N]",
    cautions="rare wording can rank high without research importance. The "
    "tfidf arm is sensitive to boilerplate; the embedding arm favors "
    "model training language.",
    validation="review tokenization, common words, phrase lengths, and the "
    "top-ranked terms by hand.",
    claims="descriptions of distinctive wording in the corpus.",
    difficulty="novice for tfidf and yake; intermediate for the embedding arm",
    teaches="`term_weighting`, `vector_similarity`",
)
_LEXICAL_BODY = _amr_body(
    "lexical-classify",
    family="labels (method 15)",
    inputs="raw_text, labels (a labeling build)",
    calibration="moderate",
    autonomy="automated_with_audit",
    run="lexical-classify [--chunk-id <id> ...]",
    cautions="shortcut words can carry the fit. Labels can attach to names, "
    "groups, or formatting instead of the concept.",
    validation="treat this as the baseline. Inspect errors. Tune features "
    "without the final test set.",
    claims="baseline performance on independent test records.",
    difficulty="intermediate",
    teaches="`classification`, `term_weighting`",
    requires="method 04, dictionary or rule-based classification (`labeling-build`)",
    next_step="method 16, frozen embeddings with a linear classifier (`classification-build`)",
)
_NEIGHBORS_BODY = _amr_body(
    "neighbors-classify",
    family="labels (method 14)",
    inputs="embeddings, labels",
    calibration="low",
    autonomy="automated_with_audit",
    run="neighbors-classify [--k N] [--chunk-id <id> ...]",
    cautions="local label errors travel. Overrepresented examples can dominate nearby votes.",
    validation="review example coverage. Tune the neighbor count and the rejection rules.",
    claims="category assignments backed by the listed neighbor examples.",
    difficulty="intermediate",
    teaches="`classification`, `vector_similarity`",
    requires="method 04, dictionary or rule-based classification (`labeling-build`)",
)
_CENTROIDS_BODY = _amr_body(
    "centroids-classify",
    family="labels (method 13)",
    inputs="embeddings, labels",
    calibration="low",
    autonomy="automated_with_audit",
    run="centroids-classify [--abstain-margin F] [--chunk-id <id> ...]",
    cautions="one mean can hide subgroups inside a category. Example "
    "selection defines the prototype.",
    validation="inspect the examples. Measure precision, recall, and "
    "abstention. Tune the margin on separate data.",
    claims="category assignments with similarities and recorded abstentions.",
    difficulty="intermediate",
    teaches="`classification`, `vector_similarity`",
    requires="method 04, dictionary or rule-based classification (`labeling-build`)",
    next_step="method 14, nearest-neighbor votes with the examples listed",
)
_CLUSTERS_BODY = _amr_body(
    "clusters-inspect",
    family="structures (method 12)",
    inputs="raw_text, embeddings",
    calibration="moderate",
    autonomy="human_in_loop",
    run="clusters-inspect [--min-cluster-size N] [--sample-cap N]",
    difficulty="intermediate",
    teaches="`clustering`, `vector_similarity`",
    next_step="method 04, dictionary or rule-based classification (`labeling-build`), "
    "once the groups suggest labels",
    cautions="a stable-looking group can reflect topic, style, speaker, or "
    "document format. The params record names the reduction that ran.",
    validation="compare representations and grouping settings. Review boundary cases by hand.",
    claims="exploratory groups with examples and distances.",
)
_DUPLICATES_BODY = _amr_body(
    "duplicates-screen",
    family="errors (method 29)",
    inputs="raw_text, embeddings",
    calibration="moderate",
    autonomy="automated_with_audit",
    run="duplicates-screen [--threshold F] [--limit N]",
    cautions="similar meaning does not make two chunks interchangeable "
    "evidence. Merging can erase independent speakers or minority wording.",
    validation="label candidate pairs by hand. Measure pairwise precision "
    "and recall at the action threshold.",
    claims="duplicate candidates at a recorded threshold, for human approval.",
    difficulty="novice",
    teaches="`vector_similarity`",
)
_OUTLIERS_BODY = _amr_body(
    "outliers-rank",
    family="errors (method 30)",
    inputs="raw_text, embeddings",
    calibration="moderate",
    autonomy="human_in_loop",
    run="outliers-rank [--k N] [--limit N]",
    cautions="results depend on the representation and the definition of "
    "normality. Minority language can look anomalous.",
    validation="define the reference group. Inspect the flagged records. "
    "Compare detectors when the stakes are high.",
    claims="a ranked review list with the detector and reference recorded.",
    difficulty="novice",
    teaches="`vector_similarity`",
)
_NLI_BODY = _amr_body(
    "nli-classify",
    family="labels (method 18)",
    inputs="raw_text, llm (an Ollama NLI model)",
    calibration="moderate",
    autonomy="automated_with_audit",
    run="nli-classify --label <label> ... [--chunk-id <id> ...] [--model <tag>]",
    cautions="wording, lexical overlap, and syntax can swing the support "
    "judgment. Label descriptions shape the results.",
    validation="test paraphrased labels, negation, and overlapping labels. "
    "Tune thresholds on labeled data.",
    claims="pilot label proposals with support scores.",
    difficulty="intermediate",
    teaches="`classification`",
    requires="a running Ollama NLI model (`corpus-kb setup` pulls it)",
    next_step="method 04, dictionary or rule-based classification (`labeling-build`), "
    "to act on the pilot labels",
)
_CONTRADICTIONS_BODY = _amr_body(
    "contradictions-screen",
    family="errors (method 28)",
    inputs="raw_text, llm, timestamps",
    calibration="moderate",
    autonomy="human_in_loop",
    run="contradictions-screen [--candidate-k N] [--limit N] [--model <tag>]",
    cautions="different times or conditions can make an apparent "
    "contradiction compatible. Candidate selection favors disagreement.",
    validation="test negation, entity identity, time, and conditions. "
    "Adjudicate the pairs by hand.",
    claims="candidate contradictions for human adjudication.",
    difficulty="intermediate",
    teaches="`contradiction_detection`, `vector_similarity`",
    requires="method 23, hybrid retrieval with reranking (`search`), for comparable candidates",
)
_SENTIMENT_BODY = _amr_body(
    "sentiment-screen",
    family="scores (method 26) and labels (method 27)",
    inputs="raw_text (method 27 also names labels)",
    calibration="moderate to high",
    autonomy="automated_with_audit to human_in_loop",
    run="sentiment-screen [--chunk-id <id> ...] [--limit N]",
    cautions="this is a preset lexicon vote, not a trained sentiment "
    "model. Expression style can be mistaken for an emotional state. "
    "Ties route to review.",
    validation="define the construct. Label and test each category, "
    "including neutral and uncertain cases. Check a sample by hand.",
    claims="screening flags for evaluative language under the defined construct.",
    difficulty="novice for method 26; intermediate for method 27",
    teaches="`sentiment_analysis`, `keyword_rules`",
)
_ACTIVE_LEARN_BODY = _amr_body(
    "active-learn-next",
    family="recommendations (method 20)",
    inputs="labels, predictions",
    calibration="high",
    autonomy="human_in_loop",
    run="active-learn-next [--strategy margin|entropy|random_incumbent] [--batch N]",
    cautions="uncertainty sampling can chase difficult or noisy cases "
    "instead of representative ones. The random_incumbent strategy "
    "guards against that bias.",
    validation="assess the improvement per annotation effort. Compare against random sampling.",
    claims="annotation-order recommendations with the strategy recorded.",
    difficulty="advanced",
    teaches="`active_learning`, `classification`",
    requires="method 16, frozen embeddings with a linear classifier (`classification-build`)",
)

# -- Chunk-coding ops (issue #350): the six routes the plugin reached only
#    through hard-coded HTTP posts, plus the status and export reads. ----
_CODING_OPS_BODY = """# corpus-coding-ops — the chunk-coding operations

Deductive coding over stored chunks: load a codebook, pool candidates,
code batches, measure reliability, calibrate floors, watch saturation.

- Load and embed a codebook JSON file (answers its codebook version id):

  ```
  corpus-kb coding embed-codebook <codebook.json>
  ```

- Run pooling and coverage reconciliation for one codebook version:

  ```
  corpus-kb coding pool --codebook-version-id <uuid>
  ```

- Code a batch of cells. The cells file holds a JSON list of
  {"chunk_id": ..., "code_ids": [...]} entries:

  ```
  corpus-kb coding code-batch --cells <cells.json> [--batch-id <id>]
  ```

- Reliability, floors, saturation, and the read ops (all flags via
  --help):

  ```
  corpus-kb coding reliability
  corpus-kb coding calibrate-floors <labels.json>
  corpus-kb coding saturation [--batch-id <id>]
  corpus-kb coding status
  corpus-kb coding export [--format csv]
  ```

- embed-codebook, pool, and code-batch are long ops: they can run for
  minutes on a real corpus.
"""

_EVENTS_BODY = """# corpus-events — event-store health

One read over the event store, the projection checkpoints, the dead
letters, and the recent ingest runs.

```
corpus-kb events [--json]
```

- Event counts by topic and the latest notification id come from the
  event store: the Postgres table, or the SQLite sidecar on the duckdb
  backend. The engine resolves the store; do not open store files.
- Checkpoints, dead letters, and the last five ingest runs are scoped
  to one tenant.
- All flags: `corpus-kb events --help`
"""

WRAPPERS: tuple[Wrapper, ...] = (
    Wrapper(
        name="corpus-ingest",
        title="corpus-ingest",
        description="Ingest research transcripts (single file or watched drop directory)",
        argument_hint="<path> [--project-id <uuid>] [--watch]",
        body=_INGEST_BODY,
        covers=("research-ingest-transcript", "research-ingest"),
    ),
    Wrapper(
        name="corpus-coding-run",
        title="corpus-coding-run",
        description="Run the deductive coding pass against a codebook version",
        argument_hint="--codebook-version-id <uuid>",
        body=_CODING_RUN_BODY,
        covers=("coding-run",),
    ),
    Wrapper(
        name="corpus-codebook-promote",
        title="corpus-codebook-promote",
        description="Promote one proposed code into a new codebook version (human gate)",
        argument_hint="--proposed-id <int> --name <name> --definition <text>",
        body=_PROMOTE_BODY,
        covers=("codebook-promote",),
    ),
    Wrapper(
        name="corpus-research-report",
        title="corpus-research-report",
        description="Emit the governance report (exhaustiveness, overlap, IRR, G3 audit)",
        argument_hint="[--level novice|expert] [--json]",
        body=_REPORT_BODY,
        covers=("research-report",),
    ),
    Wrapper(
        name="corpus-review",
        title="corpus-review",
        description="Execute accept/override decisions on the review queue",
        argument_hint="<assignment-id> --reviewer <you>",
        body=_REVIEW_BODY,
        covers=("review-accept", "review-override"),
    ),
    Wrapper(
        name="corpus-demo",
        title="corpus-demo",
        description="Run the full pipeline on the bundled demo corpus",
        argument_hint="",
        body=_DEMO_BODY,
        covers=("research-demo",),
    ),
    Wrapper(
        name="corpus-research-cycle",
        title="corpus-research-cycle",
        description="Run the research cycle with in/on/out-of-loop postures and halt gates",
        argument_hint="--mode in|on|out [--guide] [--watch <dir>]",
        body=_CYCLE_BODY,
        covers=("research-cycle",),
    ),
    Wrapper(
        name="corpus-release",
        title="corpus-release",
        description="Release lifecycle: assess, request, review, release, status, compare",
        argument_hint="--codebook-version-id <uuid> [--release-id <uuid>] [--approver <you>]",
        body=_RELEASE_BODY,
        covers=(
            "release-assess",
            "release-request",
            "release-review",
            "release-execute",
            "release-status",
            "release-compare",
        ),
    ),
    Wrapper(
        name="corpus-ingest-front-door",
        title="corpus-ingest-front-door",
        description="Top-level ingest front door: files/dirs, watch auto-heal, status, reembed",
        argument_hint="<path> [--watch] [--source-type <strategy>] [--reembed]",
        body=_FRONT_DOOR_BODY,
        covers=("ingest-status", "ingest-reembed", "ingest-reingest"),
    ),
    Wrapper(
        name="corpus-topics",
        title="corpus-topics",
        description="Topic layer: offline BERTopic build + list over stored embeddings",
        argument_hint="[--limit N]",
        body=_TOPICS_BODY,
        covers=("topics-build", "topics-list", "topics-trends"),
    ),
    Wrapper(
        name="corpus-labeling",
        title="corpus-labeling",
        description=(
            "Labeling layer: build chunk labels, list labels and labeling "
            "functions, triage weak labels, gold-set check"
        ),
        argument_hint="[--limit N]",
        body=_LABELING_BODY,
        covers=(
            "labeling-build",
            "labeling-list",
            "labeling-lfs",
            "labeling-triage",
            "labeling-gold-check",
        ),
    ),
    Wrapper(
        name="corpus-classification",
        title="corpus-classification",
        description="Classification layer: few-shot build over a seed set + predictions list",
        argument_hint="[--seed-file <path>] [--limit N]",
        body=_CLASSIFICATION_BODY,
        covers=("classification-build", "classification-list"),
    ),
    Wrapper(
        name="corpus-analysis",
        title="corpus-analysis",
        description="Analysis program: date backfill + the composed analysis run",
        argument_hint="[--output <path>]",
        body=_ANALYSIS_BODY,
        covers=("analysis-backfill-dates", "analyze-run"),
    ),
    # -- AMR program (wave 2): one card per register-backed method -----------
    Wrapper(
        name="corpus-analysis-select-method",
        title="corpus-analysis-select-method",
        description="Rank the register's methods for a stated task (the axis walk)",
        argument_hint="<task> [--output-family <fam>] [--input <kind>] [--max-tier <tier>]",
        body=_SELECT_BODY,
        covers=("analysis-select-method",),
    ),
    Wrapper(
        name="corpus-analysis-route",
        title="corpus-analysis-route",
        description="Route a plain-language question to an analysis method (the front door)",
        argument_hint="<question> [--method-id <id>]",
        body=_ROUTE_BODY,
        covers=("analysis-route",),
    ),
    Wrapper(
        name="corpus-keyphrases-extract",
        title="corpus-keyphrases-extract",
        description="Keyphrase candidates ranked three ways (tfidf, embedding, llm)",
        argument_hint="[--variant tfidf|embedding|llm] [--limit N]",
        body=_KEYPHRASES_BODY,
        covers=("keyphrases-extract",),
    ),
    Wrapper(
        name="corpus-lexical-classify",
        title="corpus-lexical-classify",
        description="TF-IDF + logistic labels trained on the stored weak labels",
        argument_hint="[--chunk-id <id> ...]",
        body=_LEXICAL_BODY,
        covers=("lexical-classify",),
    ),
    Wrapper(
        name="corpus-neighbors-classify",
        title="corpus-neighbors-classify",
        description="kNN vote labels with the neighbor evidence listed",
        argument_hint="[--k N] [--chunk-id <id> ...]",
        body=_NEIGHBORS_BODY,
        covers=("neighbors-classify",),
    ),
    Wrapper(
        name="corpus-centroids-classify",
        title="corpus-centroids-classify",
        description="Nearest-centroid labels with recorded abstentions",
        argument_hint="[--abstain-margin F] [--chunk-id <id> ...]",
        body=_CENTROIDS_BODY,
        covers=("centroids-classify",),
    ),
    Wrapper(
        name="corpus-clusters-inspect",
        title="corpus-clusters-inspect",
        description="Cluster structure report: sizes, noise, exemplars, reduction",
        argument_hint="[--min-cluster-size N] [--sample-cap N]",
        body=_CLUSTERS_BODY,
        covers=("clusters-inspect",),
    ),
    Wrapper(
        name="corpus-duplicates-screen",
        title="corpus-duplicates-screen",
        description="Near-duplicate candidate pairs at a cosine threshold",
        argument_hint="[--threshold F] [--limit N]",
        body=_DUPLICATES_BODY,
        covers=("duplicates-screen",),
    ),
    Wrapper(
        name="corpus-outliers-rank",
        title="corpus-outliers-rank",
        description="Chunks ranked by k-th-nearest-neighbor distance",
        argument_hint="[--k N] [--limit N]",
        body=_OUTLIERS_BODY,
        covers=("outliers-rank",),
    ),
    Wrapper(
        name="corpus-nli-classify",
        title="corpus-nli-classify",
        description="Zero-shot labels via entailment judgments",
        argument_hint="--label <label> ... [--limit N] [--model <tag>]",
        body=_NLI_BODY,
        covers=("nli-classify",),
    ),
    Wrapper(
        name="corpus-contradictions-screen",
        title="corpus-contradictions-screen",
        description="NLI contradiction candidates for human adjudication",
        argument_hint="[--candidate-k N] [--limit N] [--model <tag>]",
        body=_CONTRADICTIONS_BODY,
        covers=("contradictions-screen",),
    ),
    Wrapper(
        name="corpus-sentiment-screen",
        title="corpus-sentiment-screen",
        description="Preset lexicon sentiment screen with the honest evidence note",
        argument_hint="[--chunk-id <id> ...] [--limit N]",
        body=_SENTIMENT_BODY,
        covers=("sentiment-screen",),
    ),
    Wrapper(
        name="corpus-survey-openend-codes",
        title="corpus-survey-openend-codes",
        description="Code open-ended survey answers per question with counts",
        argument_hint="[--chunk-id <id> ...] [--min-cluster-size N]",
        body=_SURVEY_CODES_BODY,
        covers=("survey-openend-codes",),
    ),
    Wrapper(
        name="corpus-usability-severity",
        title="corpus-usability-severity",
        description="Score findings with the fixed 0-4 severity rubric",
        argument_hint="[--chunk-id <id> ...] [--limit N]",
        body=_SEVERITY_BODY,
        covers=("usability-severity",),
    ),
    Wrapper(
        name="corpus-emotion-tag",
        title="corpus-emotion-tag",
        description="Tag chunks with the fixed eight-emotion set",
        argument_hint="[--chunk-id <id> ...] [--limit N] [--model <tag>]",
        body=_EMOTION_BODY,
        covers=("emotion-tag",),
    ),
    Wrapper(
        name="corpus-active-learn-next",
        title="corpus-active-learn-next",
        description="Next human-labeling batch ranked from stored predictions",
        argument_hint="[--strategy margin|entropy|random_incumbent] [--batch N]",
        body=_ACTIVE_LEARN_BODY,
        covers=("active-learn-next",),
    ),
    # -- Chunk-coding ops (issue #350): one card for the eight coding ops ---
    Wrapper(
        name="corpus-coding-ops",
        title="corpus-coding-ops",
        description=(
            "Chunk-coding ops: codebook load, pool, batch, reliability, "
            "floors, saturation, status, export"
        ),
        argument_hint="<file> | --codebook-version-id <uuid> | --cells <file>",
        body=_CODING_OPS_BODY,
        covers=(
            "coding-embed-codebook",
            "coding-pool",
            "coding-code-batch",
            "coding-reliability",
            "coding-calibrate-floors",
            "coding-saturation",
            "coding-status",
            "coding-export",
        ),
    ),
    Wrapper(
        name="corpus-events",
        title="corpus-events",
        description="Event-store health: topics, notification id, checkpoints, dead letters, runs",
        argument_hint="[--json]",
        body=_EVENTS_BODY,
        covers=("events-summary",),
    ),
)

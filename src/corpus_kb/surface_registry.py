"""Corpus research/coding surface registry — the ONE source of truth (todo 18).

Every research/coding surface gets one row: its CLI subcommand path, its MCP
tool name, the HTTP route where one exists, a one-line description. The
harness command pack (``scripts/gen_harness_pack.py``) is GENERATED from the
WRAPPERS below — never hand-written (r7.2 single-source sync); editing a
generated wrapper without a registry change fails ``--check``.

STRICTLY STDLIB-ONLY: zero ``corpus_kb`` or third-party imports (r9 — the
agent-config-consistency CI job installs NO dependencies, so any import here
is a ModuleNotFoundError the local bar cannot catch). Consumers load this
file by path (``importlib.util.spec_from_file_location``), not as a package
member.
"""

from __future__ import annotations

from typing import NamedTuple

# --------------------------------------------------------------------------
# Surfaces: one row per research/coding command pair (CLI + MCP)
# --------------------------------------------------------------------------


class Surface(NamedTuple):
    """One research/coding surface (CLI subcommand + MCP tool).

    NamedTuple (not dataclass) so any file-path loader works WITHOUT
    registering the module in ``sys.modules`` first — dataclass annotation
    resolution requires the module to be importable by name.
    """

    name: str
    cli_path: str
    mcp_tool: str
    description: str
    http_route: str | None = None  # set for every surface outside the parity allowlist


SURFACES: tuple[Surface, ...] = (
    Surface(
        name="research-ingest-transcript",
        cli_path="research ingest-transcript",
        mcp_tool="research_ingest_transcript",
        description="Ingest ONE transcript (txt/vtt/srt/csv/docx): turns, roles, exchanges.",
        http_route="/api/research/ingest-transcript",
    ),
    Surface(
        name="research-ingest",
        cli_path="research ingest",
        mcp_tool="research_ingest",
        description="Dynamic ingest of file/dir/glob with dedup; --watch tails a drop dir.",
        http_route="/api/research/ingest",
    ),
    Surface(
        name="coding-run",
        cli_path="coding run",
        mcp_tool="coding_run",
        description="Deductive run (v5 §8): three-view scoring, calibrated tau, conformal.",
        http_route="/api/coding/run",
    ),
    Surface(
        name="coding-inductive",
        cli_path="coding inductive",
        mcp_tool="inductive_run",
        description="Inductive pass (v5 §9): summaries, UMAP/HDBSCAN clusters, proposed codes.",
        http_route="/api/coding/inductive",
    ),
    Surface(
        name="codebook-promote",
        cli_path="codebook promote",
        mcp_tool="codebook_promote",
        description="Promote a proposed code into a NEW codebook version (human gate).",
        http_route="/api/codebook/promote",
    ),
    Surface(
        name="research-report",
        cli_path="research report",
        mcp_tool="research_report",
        description="Governance report (v5 §11/13/14): exhaustiveness, overlap, IRR, G3.",
        http_route="/api/research/report",
    ),
    Surface(
        name="review-accept",
        cli_path="review accept",
        mcp_tool="review_execute",
        description="Confirm the model's assignment for one CodingAssignment (human review).",
        http_route="/api/review/execute",
    ),
    Surface(
        name="review-override",
        cli_path="review override",
        mcp_tool="review_execute",
        description="Overrule the model's assignment for one CodingAssignment.",
        http_route="/api/review/execute",
    ),
    Surface(
        name="notebook-ask",
        cli_path="research ask",
        mcp_tool="notebook_ask",
        description="Grounded Q&A: cited sentences; retrieval-only mode = zero LLM calls.",
        http_route="/api/notebook/ask",
    ),
    Surface(
        name="notebook-evidence",
        cli_path="research evidence",
        mcp_tool="notebook_evidence",
        description="Evidence for one code: ranked units with flags + conformal sizes.",
        http_route="/api/notebook/evidence",
    ),
    Surface(
        name="notebook-uncoded",
        cli_path="research uncoded",
        mcp_tool="notebook_uncoded",
        description="Uncoded units ranked by nearest-code margin (the no-missing-clusters radar).",
        http_route="/api/notebook/uncoded",
    ),
    Surface(
        name="notebook-overlap",
        cli_path="research overlap",
        mcp_tool="notebook_overlap",
        description="Code overlap: flagged pairs, confusion, borderline-margin units.",
        http_route="/api/notebook/overlap",
    ),
    Surface(
        name="research-cycle",
        cli_path="research cycle",
        mcp_tool="research_cycle",
        description=(
            "Research cycle: full pipeline with halt gates; long-running, "
            "synchronous on every surface (promotion is human-gated)."
        ),
        http_route="/api/research/cycle",
    ),
    # The narrated demo: every surface runs the same pipeline; the CLI
    # narrates, http/mcp/socket return the receipt envelope (long-running,
    # synchronous — see contracts.catalogue_research).
    Surface(
        name="research-demo",
        cli_path="research demo",
        mcp_tool="research_demo",
        description="Narrated demo pipeline on the bundled demo corpus; long-running.",
        http_route="/api/research/demo",
    ),
    # -- release lifecycle (U5, P7, v6 §6): full cli+mcp+http parity --------
    Surface(
        name="release-assess",
        cli_path="release assess",
        mcp_tool="release_assess",
        description="Compose every release gate over current evidence; WRITES NO RELEASE.",
        http_route="/api/release/assess",
    ),
    Surface(
        name="release-request",
        cli_path="release request",
        mcp_tool="release_request",
        description="Open a draft release pinned to the current codebook content hash.",
        http_route="/api/release/request",
    ),
    Surface(
        name="release-review",
        cli_path="release review",
        mcp_tool="release_review",
        description="Review packet: evidence, failing/not_evaluable gates, waivers, next actions.",
        http_route="/api/release/review",
    ),
    Surface(
        name="release-execute",
        cli_path="release release",
        mcp_tool="release_execute",
        description="Evaluate + record gates, apply waivers, execute the release (approver).",
        http_route="/api/release/release",
    ),
    Surface(
        name="release-status",
        cli_path="release status",
        mcp_tool="release_status",
        description="Stage, release state, blockers, sufficiency flags, the next command.",
        http_route="/api/release/status",
    ),
    Surface(
        name="release-compare",
        cli_path="release compare",
        mcp_tool="release_compare",
        description="Diff two releases' manifests (codes, definitions, methods, metrics).",
        http_route="/api/release/compare",
    ),
    # -- ingest front door (Stage 5): full cli+mcp+http parity ---------------
    Surface(
        name="ingest-status",
        cli_path="ingest-status",
        mcp_tool="ingest_status",
        description="Recent ingest_runs ledger entries + degraded-chunk counts (honest history).",
        http_route="/api/ingest/status",
    ),
    Surface(
        name="ingest-reembed",
        cli_path="ingest-reembed",
        mcp_tool="ingest_reembed",
        description="Re-embed degraded (zero-vector) chunks once the provider is back.",
        http_route="/api/ingest/reembed",
    ),
    # -- pipeline ingest ops (UI SPA Task 5a): the manifest gap closed -------
    Surface(
        name="ingest-directory",
        cli_path="ingest",
        mcp_tool="ingest_directory",
        description="Ingest the supported files under one directory in one walk.",
        http_route="/api/ingest/directory",
    ),
    Surface(
        name="ingest-url",
        cli_path="ingest-url",
        mcp_tool="ingest_url",
        description="Fetch (or crawl) a website and ingest its HTML pages.",
        http_route="/api/ingest/url",
    ),
    # -- UI SPA Task 5a: demo-load + doctor (CLI + HTTP only) ----------------
    # No MCP tool or socket method exists for these two, so mcp_tool stays
    # empty and no Wrapper fronts them; the contracts SKIP set declares the
    # same real surfaces (byte-matched by test_catalogue_matches_surface_
    # registry).
    Surface(
        name="demo-load",
        cli_path="demo-kb",
        mcp_tool="",
        description="Ingest the bundled sample knowledge folder (house receipt).",
        http_route="/api/demo/load",
    ),
    Surface(
        name="doctor",
        cli_path="doctor",
        mcp_tool="",
        description="Read-only diagnostics: profile, Postgres, Ollama, research probes.",
        http_route="/api/doctor",
    ),
    # -- topics layer (Wave 4): full cli+mcp+http parity ----------------------
    Surface(
        name="topics-build",
        cli_path="topics-build",
        mcp_tool="topics_build",
        description="Cluster stored chunk embeddings into topics (offline BERTopic).",
        http_route="/api/topics/build",
    ),
    Surface(
        name="topics-list",
        cli_path="topics-list",
        mcp_tool="topics_list",
        description="List the built topics with sizes, labels, and keywords.",
        http_route="/api/topics",
    ),
    # -- analysis program (Wave 0 skeleton): full cli+mcp+http parity --------
    Surface(
        name="topics-trends",
        cli_path="topics-trends",
        mcp_tool="topics_trends",
        description="Per-topic frequency series over binned document dates.",
        http_route="/api/topics/trends",
    ),
    Surface(
        name="labeling-build",
        cli_path="labeling-build",
        mcp_tool="labeling_build",
        description="Score the labeling functions and write the aggregated chunk labels.",
        http_route="/api/labeling/build",
    ),
    Surface(
        name="labeling-list",
        cli_path="labeling-list",
        mcp_tool="labeling_list",
        description="List the built chunk labels with probabilities.",
        http_route="/api/labeling",
    ),
    Surface(
        name="labeling-lfs",
        cli_path="labeling-lfs",
        mcp_tool="labeling_lfs",
        description="List the registered labeling functions with kinds and coverage.",
        http_route="/api/labeling/lfs",
    ),
    Surface(
        name="classification-build",
        cli_path="classification-build",
        mcp_tool="classification_build",
        description="Fit the few-shot classifier against a seed set; write predictions.",
        http_route="/api/classification/build",
    ),
    Surface(
        name="classification-list",
        cli_path="classification-list",
        mcp_tool="classification_list",
        description="List the built chunk predictions, bounded by limit.",
        http_route="/api/classification",
    ),
    Surface(
        name="analysis-backfill-dates",
        cli_path="analysis-backfill-dates",
        mcp_tool="analysis_backfill_dates",
        description="Backfill documents.document_date from chunk metadata (idempotent).",
        http_route="/api/analysis/backfill-dates",
    ),
    Surface(
        name="analyze-run",
        cli_path="analyze-run",
        mcp_tool="analyze_run",
        description="Run the composed corpus analysis and write the report.",
        http_route="/api/analyze",
    ),
    # -- analysis method register program (wave 2): full cli+mcp+http --------
    Surface(
        name="analysis-select-method",
        cli_path="analysis-select-method",
        mcp_tool="analysis_select_method",
        description="Rank the register's methods for a stated task (pure registry logic).",
        http_route="/api/analysis/select-method",
    ),
    Surface(
        name="keyphrases-extract",
        cli_path="keyphrases-extract",
        mcp_tool="keyphrases_extract",
        description="Rank keyphrase candidates (tfidf, embedding, or llm variant).",
        http_route="/api/analysis/keyphrases-extract",
    ),
    Surface(
        name="lexical-classify",
        cli_path="lexical-classify",
        mcp_tool="lexical_classify",
        description="TF-IDF + logistic labels trained on the stored weak labels.",
        http_route="/api/analysis/lexical-classify",
    ),
    Surface(
        name="neighbors-classify",
        cli_path="neighbors-classify",
        mcp_tool="neighbors_classify",
        description="kNN vote labels over the labeled chunks, neighbors listed.",
        http_route="/api/analysis/neighbors-classify",
    ),
    Surface(
        name="centroids-classify",
        cli_path="centroids-classify",
        mcp_tool="centroids_classify",
        description="Nearest-centroid labels with recorded abstentions.",
        http_route="/api/analysis/centroids-classify",
    ),
    Surface(
        name="clusters-inspect",
        cli_path="clusters-inspect",
        mcp_tool="clusters_inspect",
        description="Cluster structure report: sizes, noise, exemplar chunk ids.",
        http_route="/api/analysis/clusters-inspect",
    ),
    Surface(
        name="duplicates-screen",
        cli_path="duplicates-screen",
        mcp_tool="duplicates_screen",
        description="Near-duplicate candidate pairs at a cosine threshold.",
        http_route="/api/analysis/duplicates-screen",
    ),
    Surface(
        name="outliers-rank",
        cli_path="outliers-rank",
        mcp_tool="outliers_rank",
        description="Chunks ranked by distance to the k-th nearest neighbor.",
        http_route="/api/analysis/outliers-rank",
    ),
    Surface(
        name="nli-classify",
        cli_path="nli-classify",
        mcp_tool="nli_classify",
        description="Zero-shot labels via entailment judgments per hypothesis.",
        http_route="/api/analysis/nli-classify",
    ),
    Surface(
        name="contradictions-screen",
        cli_path="contradictions-screen",
        mcp_tool="contradictions_screen",
        description="Candidate pairs the NLI model marks CONTRADICT, for human review.",
        http_route="/api/analysis/contradictions-screen",
    ),
    Surface(
        name="sentiment-screen",
        cli_path="sentiment-screen",
        mcp_tool="sentiment_screen",
        description="Preset lexicon sentiment screen (vote aggregation, honest note).",
        http_route="/api/analysis/sentiment-screen",
    ),
    Surface(
        name="active-learn-next",
        cli_path="active-learn-next",
        mcp_tool="active_learn_next",
        description="Rank stored predictions for the next human-labeling batch.",
        http_route="/api/analysis/active-learn-next",
    ),
    # -- core search op (the last surface it was missing): full cli+mcp+http --
    Surface(
        name="search",
        cli_path="search",
        mcp_tool="search",
        description="Hybrid vector + full-text search with the degraded envelope.",
        http_route="/api/search",
    ),
    # -- ask operation (UX program Wave 1): full cli+mcp+http parity ----------
    Surface(
        name="ask",
        cli_path="ask",
        mcp_tool="ask",
        description="Answer a question from retrieved evidence with cited sentences.",
        http_route="/api/ask",
    ),
)

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
  docs/getting-started.md "Before you start").
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
# Each card teaches the axis walk (the register's selection rules): state
# the question, match the output family, check the inputs, budget
# calibration and autonomy, read the cautions, run, then attach the
# validation and the permitted claims. Axis facts come from
# config/analysis_methods.yaml (the register rows for the op), verbatim
# in meaning; the docs table is docs/ANALYSIS-METHODS.md.

_AMR_WALK = """## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: {family}.
3. Check the inputs it needs: {inputs}.
4. Budget the calibration effort ({calibration}) and autonomy ({autonomy}).
5. Read the cautions. Run. Then do the validation step.

"""

_AMR_FOOT = """- Cautions: {cautions}
- Validation: {validation}
- Claim only: {claims}
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb {cli} --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
"""


def _amr_body(
    name: str,
    family: str,
    inputs: str,
    calibration: str,
    autonomy: str,
    run: str,
    cautions: str,
    validation: str,
    claims: str,
) -> str:
    """One method card: axis walk + run + cautions (all plain English)."""
    return (
        f"# corpus-{name} — analysis method card\n\n"
        + _AMR_WALK.format(family=family, inputs=inputs, calibration=calibration, autonomy=autonomy)
        + f"## Run\n\n  ```\n  corpus-kb {run}\n  ```\n\n"
        + _AMR_FOOT.format(cautions=cautions, validation=validation, claims=claims, cli=name)
    )


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
)
_CLUSTERS_BODY = _amr_body(
    "clusters-inspect",
    family="structures (method 12)",
    inputs="raw_text, embeddings",
    calibration="moderate",
    autonomy="human_in_loop",
    run="clusters-inspect [--min-cluster-size N] [--sample-cap N]",
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
)

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
        covers=("ingest-status", "ingest-reembed"),
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
        description="Labeling layer: build chunk labels, list labels and labeling functions",
        argument_hint="[--limit N]",
        body=_LABELING_BODY,
        covers=("labeling-build", "labeling-list", "labeling-lfs"),
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
        name="corpus-active-learn-next",
        title="corpus-active-learn-next",
        description="Next human-labeling batch ranked from stored predictions",
        argument_hint="[--strategy margin|entropy|random_incumbent] [--batch N]",
        body=_ACTIVE_LEARN_BODY,
        covers=("active-learn-next",),
    ),
)

ACTIVE_WRAPPERS: tuple[Wrapper, ...] = tuple(w for w in WRAPPERS if not w.planned)

# Description MUST byte-match the existing mcp-configs/*.json entries
# (check_agent_configs compares descriptions across editors).
SERVER_NAME = "corpus-kb"
SERVER_DESCRIPTION = (
    "Local RAG system \u2014 search, ingest, knowledge graph, "
    "SQL queries over your code and documents"
)
SERVER_COMMAND = "corpus-kb"
SERVER_ARGS: tuple[str, ...] = ("--transport", "stdio")
# autoApprove entries for emitted editor templates (legacy master tool set,
# kept in lockstep with claude-code.json).
SERVER_AUTO_APPROVE: tuple[str, ...] = (
    "search",
    "search_context",
    "search_similar",
    "retrieve_context",
    "list_documents",
    "get_stats",
    "list_versions",
    "list_branches",
    "get_entity_relations",
    "search_graph",
    "sql_query",
    "sql_tables",
    "get_document_tags",
    "get_metadata",
    "query_document_stats",
    "sync_database",
    # Stage-5 ingest front door surfaces (tools/ingest_surface_tools.py)
    "ingest_status",
    "ingest_reembed",
    # Wave 4 topics layer (tools/topics_tools.py)
    "topics_build",
    "topics_list",
    # Corpus analysis program (tools/labeling_tools.py,
    # tools/classification_tools.py, tools/analysis_tools.py, and the
    # topics_trends stub in tools/topics_tools.py)
    "topics_trends",
    "labeling_build",
    "labeling_list",
    "labeling_lfs",
    "classification_build",
    "classification_list",
    "analysis_backfill_dates",
    "analyze_run",
    # Ask operation (UX Wave 1, tools/ask_tools.py)
    "ask",
)

# Marker fences for the generated .claude/CLAUDE.md research-commands section.
CLAUDEMD_START = "<!-- corpus-harness:research-commands:start -->"
CLAUDEMD_END = "<!-- corpus-harness:research-commands:end -->"


def claudemd_section() -> str:
    """Render the research-commands section injected into .claude/CLAUDE.md."""
    lines = [
        CLAUDEMD_START,
        "## Research command pack (generated — do not edit)",
        "",
        "Every research/coding surface, generated from `src/corpus_kb/surface_registry.py`",
        "by `scripts/gen_harness_pack.py`. Bodies live in `.claude/commands/` and",
        "`.opencode/command/`; editing a wrapper without a registry change fails",
        "`scripts/gen_harness_pack.py --check`.",
        "",
        "| Harness command | CLI path | MCP tool | Purpose |",
        "|-----------------|----------|----------|---------|",
    ]
    surface_by_name = {s.name: s for s in SURFACES}
    for wrapper in ACTIVE_WRAPPERS:
        for surface_name in wrapper.covers:
            surface = surface_by_name[surface_name]
            tool = f"`{surface.mcp_tool}`" if surface.mcp_tool else "(none)"
            lines.append(
                f"| `{wrapper.name}` | `corpus-kb {surface.cli_path}` "
                f"| {tool} | {surface.description} |"
            )
    planned = [w for w in WRAPPERS if w.planned]
    if planned:
        names = ", ".join(f"`{w.name}`" for w in planned)
        lines += ["", f"Planned wrappers (not emitted yet): {names}."]
    lines += [CLAUDEMD_END, ""]
    return "\n".join(lines)

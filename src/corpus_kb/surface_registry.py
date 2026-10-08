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
    http_route: str | None = None  # none today: research surfaces are CLI/MCP only


SURFACES: tuple[Surface, ...] = (
    Surface(
        name="research-ingest-transcript",
        cli_path="research ingest-transcript",
        mcp_tool="research_ingest_transcript",
        description="Ingest ONE transcript (txt/vtt/srt/csv/docx): turns, roles, exchanges.",
    ),
    Surface(
        name="research-ingest",
        cli_path="research ingest",
        mcp_tool="research_ingest",
        description="Dynamic ingest of file/dir/glob with dedup; --watch tails a drop dir.",
    ),
    Surface(
        name="coding-run",
        cli_path="coding run",
        mcp_tool="coding_run",
        description="Deductive run (v5 §8): three-view scoring, calibrated tau, conformal.",
    ),
    Surface(
        name="coding-inductive",
        cli_path="coding inductive",
        mcp_tool="inductive_run",
        description="Inductive pass (v5 §9): summaries, UMAP/HDBSCAN clusters, proposed codes.",
    ),
    Surface(
        name="codebook-promote",
        cli_path="codebook promote",
        mcp_tool="codebook_promote",
        description="Promote a proposed code into a NEW codebook version (human gate).",
    ),
    Surface(
        name="research-report",
        cli_path="research report",
        mcp_tool="research_report",
        description="Governance report (v5 §11/13/14): exhaustiveness, overlap, IRR, G3.",
    ),
    Surface(
        name="review-accept",
        cli_path="review accept",
        mcp_tool="review_execute",
        description="Confirm the model's assignment for one CodingAssignment (human review).",
    ),
    Surface(
        name="review-override",
        cli_path="review override",
        mcp_tool="review_execute",
        description="Overrule the model's assignment for one CodingAssignment.",
    ),
    Surface(
        name="notebook-ask",
        cli_path="research ask",
        mcp_tool="notebook_ask",
        description="Grounded Q&A: cited sentences; retrieval-only mode = zero LLM calls.",
    ),
    Surface(
        name="notebook-evidence",
        cli_path="research evidence",
        mcp_tool="notebook_evidence",
        description="Evidence for one code: ranked units with flags + conformal sizes.",
    ),
    Surface(
        name="notebook-uncoded",
        cli_path="research uncoded",
        mcp_tool="notebook_uncoded",
        description="Uncoded units ranked by nearest-code margin (the no-missing-clusters radar).",
    ),
    Surface(
        name="notebook-overlap",
        cli_path="research overlap",
        mcp_tool="notebook_overlap",
        description="Code overlap: flagged pairs, confusion, borderline-margin units.",
    ),
    Surface(
        name="research-cycle",
        cli_path="research cycle",
        mcp_tool="research_cycle",
        description=(
            "Research cycle (todo 20): the full pipeline with in/on/out-of-loop "
            "postures and human-critical halt gates (promotion is hard-floored)."
        ),
    ),
    # CLI-ONLY (r13): no MCP tool - agents do not need a narrated demo, and a
    # tool here would red validate-configs/agent-config-consistency untouched
    # by this surface. Empty mcp_tool renders as (none) in the CLAUDE.md table.
    Surface(
        name="research-demo",
        cli_path="research demo",
        mcp_tool="",
        description="Narrated end-to-end pipeline on the bundled demo corpus (CLI only).",
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
- CLI-only by design; all flags: `corpus-kb research demo --help`
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
- All flags: `corpus-kb research cycle --help`
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

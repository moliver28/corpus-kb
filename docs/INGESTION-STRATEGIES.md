# Ingestion strategies

Corpus-KB ingests through ONE seam. Everything above storage consumes a
`ChunkPlan` (ordered chunk drafts + per-chunk metadata + strategy name +
detection provenance). Everything below it — embed, extract, store — is
strategy-agnostic and never changes when a new modality is added. Adding
ASR output, web crawls, slides, notebooks, or tables is one module each;
`run_pipeline` is never touched again.

This page is the extension guide. For what the pipeline does after the
strategy hands over, see [INGESTION.md](INGESTION.md).

---

## The contract

A strategy implements the `IngestionStrategy` protocol
(`src/corpus_kb/ingestion/base.py`):

```python
class IngestionStrategy(Protocol):
    name: str

    def detect(self, source_type_hint, filename, head) -> DetectResult: ...

    def build_chunks(self, raw, filename, profile) -> ChunkPlan: ...
```

- `detect()` scores how well the input (explicit hint, filename, and a text
  head) fits the strategy's modality and returns
  `DetectResult(confidence, reason)`. The reason is human-readable — it is
  printed at ingest time, e.g.
  `detected: transcript (0.92, moderator-pattern) → strategy transcript`.
- `build_chunks()` turns raw content into a `ChunkPlan`. Strategies that
  don't fit the element model (like `transcript`) bypass the `unstructured`
  partitioner entirely and build chunks directly.

Registry and discovery live in `src/corpus_kb/ingestion/registry.py`:
`register_strategy()` for in-process registration and `importlib.metadata`
entry-point discovery for packaged plugins.

## Built-ins

| Strategy | What it does |
|----------|--------------|
| `default` | `unstructured` partition plus the element-to-chunk mapper. Each chunk is one element with heading paths, line numbers, and exact char offsets. Oversized elements split on exact slices. Behavior is verbatim identical to the pre-strategy pipeline. |
| `transcript` | Turn-aligned question+answer chunks from speaker-turn transcripts. Each chunk carries `qa_context` ({question, participants, exchange_id}) and the same token guard as everything else. |
| `pptx_structure` | One chunk per slide: title breadcrumbs, tables as GitHub markdown, and speaker notes as their own chunks. Core/extended-properties metadata (author, created/modified, company, subject, slide numbers) rides on every chunk. Oversized slides split at paragraph boundaries. |
| `web_structure` | Heading-section chunks from fetched HTML (`corpus-kb ingest-url` does the fetching: robots.txt, size/content-type caps, optional same-domain crawl). Every chunk carries page metadata (OG/Twitter/JSON-LD/canonical, language) plus its heading breadcrumb; nav/header/footer chrome is excluded. |
| `survey` | CSV question/answer exports (Wave 5). Each answer is one chunk. The question text rides in chunk metadata (`question`, `row`, `answer_column`), so a retrieved answer carries its question. Detection claims a `.csv` whose header has a question-like and an answer-like column; other CSVs keep the default strategy. |
| `markdown_sections` | Raw markdown text segmented at ATX/setext heading boundaries (Wave 5). Every chunk is an exact char-span slice of the canonical text and carries the heading breadcrumb. Markdown FILES keep the default partition — detection never claims them — so only the degenerate raw-text path changes. |
| `code` (optional) | Tree-sitter AST chunking for source files (Wave 5). Each top-level definition is one chunk. Adjacent small siblings pack up to the token budget. Spans are exact char offsets. Needs `pip install 'corpus-kb[code]'`. Without the extra, code files use the default plan and the result carries a warnings entry. |
| `textbook` (optional) | Windowed parsing for large PDFs (textbook program). One Docling parse per page window, a disk cache, pypdf outline enrichment, and section titles that carry across window edges. Full parse needs the `[docling]` extra; see the section below. |

## The textbook strategy

Large textbooks are long jobs. One whole-book parse holds a worker for
hours and loses everything on a crash. The textbook strategy splits the
job. It parses a PDF in page windows, caches each parsed window, and
assembles chunks after the last window lands.

**The gate.** The strategy claims PDFs. Detection sees only the file
name and the text head. The real page-count gate runs when the
strategy builds chunks. A PDF with at least `textbook.min_pages` pages
(default 120) takes the windowed path. A shorter PDF falls back to the
default plan, and the result records why. `corpus-kb textbook scan`
runs the same signals up front, so you can see the candidates before
you enqueue anything.

**Windows.** Each parse call covers `textbook.window_pages` pages
(default 50). One window is one Docling `convert` call. Memory stays
bounded, and one book cannot hold a worker for the whole run.

**The parse cache.** Each parsed window caches as JSON under
`textbook.cache_dir`. The cache key is the parser version, the file
SHA-256, and the window index. A retry or a resume loads its windows
instead of paying the parse again. A changed file, a changed parser, or
a corrupted record misses and re-parses. Nothing prunes the cache
today: files add up, and a changed book or parser leaves its old files
in place. To clear the cache, delete the directory. That is safe at any
time; the next run re-parses what it misses and only loses time.

**Outline enrichment.** A windowed parse sees no global structure; PDF
bookmarks are the exception. The strategy reads the pypdf outline and
uses bookmark titles on pages where Docling labels are thin.

**The seam, stated plainly.** When a window starts mid-section, the
previous window's open section title carries. A section that spans many
windows keeps its opening title. A header Docling missed inside a later
window stays unfilled. Content repeated across a window boundary dedupes
by page, first window wins.

**Degrade paths, never a crash.** Without the `[docling]` extra, the
strategy warns once and parses windows with pypdf only. Page text,
outline titles, and page metadata survive. Without the `[scan]` extra,
or below `min_pages`, it falls back to the default plan.

| Key | Default | Description |
|-----|---------|-------------|
| `textbook.min_pages` | `120` | The page-count gate for the windowed path. Whole number, 1 or more |
| `textbook.window_pages` | `50` | Pages per parse window. Whole number, 1 or more |
| `textbook.parser` | `docling` | The page parser: `docling` or `pypdf` |
| `textbook.cache_dir` | `~/.corpus-kb/textbook-cache` | The parsed-window cache root |

Books are long jobs, so most people run them through the ingest queue
([HARNESS-PLAYBOOKS.md](HARNESS-PLAYBOOKS.md), "Recipe: queue the
textbooks"). The queue worker drains one book at a time.

## Three extension ladders

Pick the lowest ladder that does the job.

### Ladder 1 — config mapping (no code)

Map any source type to an existing strategy in `config.yaml`. The right
answer whenever your data matches a strategy that already exists.

```yaml
ingestion:
  default: default
  strategies:
    transcript: transcript      # built-in
    call-recording: transcript  # any source_type can reuse any strategy
    survey: survey              # shipped default (Wave 5)
```

### Ladder 2 — in-process registration (scripts, power users)

Write a small Python script that registers your strategy before ingesting:

```python
from corpus_kb.ingestion.registry import register_strategy

register_strategy(MeetingNotesStrategy())
```

Nothing is packaged; the registration lives as long as the process.

### Ladder 3 — entry-point plugin (packaged projects)

A downstream project declares the entry point in its own `pyproject.toml`:

```toml
[project.entry-points."corpus_kb.ingestion_strategies"]
meeting-notes = "my_pkg.ingestion:MeetingNotesStrategy"
```

Corpus-KB discovers the group at startup via `importlib.metadata` and the
strategy participates in detection and config mapping like a built-in.
Zero core edits, ever.

## Selection precedence

The strategy for an ingest is resolved once, in this order:

| Precedence | Source | Example |
|------------|--------|---------|
| 1 | explicit `--source-type` override | `corpus-kb ingest call.txt --source-type transcript` |
| 2 | `ingestion.strategies[source_type]` config map | the Ladder-1 YAML above |
| 3 | content detection | each registered strategy's `detect()` scores the input; highest confidence wins |
| 4 | `ingestion.default` | `default` |

The resolved choice (strategy, confidence, reason) is printed at ingest and
recorded in the pipeline result. Every document therefore carries its detection
provenance.

## Conformance invariants

Every registered strategy — built-in or third-party — must produce a
`ChunkPlan` that passes the conformance suite:

| Invariant | Rule |
|-----------|------|
| Non-empty | At least one chunk draft, or an explicit `empty_reason` saying why none. |
| Continuous indices | Chunk indices are `0..n-1` with no gaps. |
| Token budget | Each chunk stays within 0.8 × 2048 tokens (chars/4 heuristic, ≈ 1638 chars). |
| Serializable metadata | Per-chunk metadata must be JSON-serializable (it is stored on the chunk). |
| `qa_context` provenance | Only the `transcript` strategy may set `qa_context` on a chunk. |

The suite runs over the registry. A broken third-party strategy therefore
fails loudly at registration/ingest time instead of silently corrupting
retrieval.

## Worked example: a `meeting-notes` strategy

About forty lines, usable from Ladder 2 or 3. It chunks meeting notes at
`## Decision` / `## Action` / `## Discussion` headings and records the
section kind as metadata.

```python
# my_pkg/ingestion.py
from __future__ import annotations

import re

from corpus_kb.ingestion.base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
)



class MeetingNotesStrategy:
    """Chunk meeting notes at decision/action/discussion headings."""

    name = "meeting-notes"
    _HEADING = re.compile(r"^## (Decision|Action|Discussion)\b.*$", re.MULTILINE)

    def detect(self, source_type_hint, filename, head):
        if source_type_hint == "meeting-notes":
            return DetectResult(confidence=1.0, reason="explicit override")
        hits = len(self._HEADING.findall(head))
        return DetectResult(
            confidence=min(0.9, 0.3 * hits),
            reason=f"{hits} meeting headings in head",
        )

    def build_chunks(self, raw, filename, profile):
        marks = list(self._HEADING.finditer(raw))
        if not marks:
            return ChunkPlan(
                strategy=self.name,
                document_text=raw,
                empty_reason="no Decision/Action/Discussion headings",
            )
        bounds = marks + [None]  # None = end of document
        chunks = []
        for start, end in zip(bounds, bounds[1:]):
            text = raw[start.start(): end.start() if end else len(raw)].strip()
            # Split oversized sections so every chunk fits the token budget.
            for j in range(0, len(text), TOKEN_BUDGET_CHARS):
                chunks.append(
                    PlannedChunk(
                        text=text[j: j + TOKEN_BUDGET_CHARS],
                        index=len(chunks),
                        metadata={"section": start.group(1).lower()},
                    )
                )
        return ChunkPlan(strategy=self.name, chunks=chunks, document_text=raw)


# Ladder 2:
#   from corpus_kb.ingestion.registry import register_strategy
#   register_strategy(MeetingNotesStrategy())
#
# Ladder 3 (your package's pyproject.toml):
#   [project.entry-points."corpus_kb.ingestion_strategies"]
#   meeting-notes = "my_pkg.ingestion:MeetingNotesStrategy"
```

Notes on the example:

- `detect()` returns high confidence only on evidence. An explicit
  override short-circuits; otherwise it counts headings in the head and
  never claims certainty it doesn't have.
- `build_chunks()` returns an explicit `empty_reason` when the document
  isn't a meeting note. It keeps indices continuous across the oversized-
  section split and stores only plain JSON metadata.
- After registering, `ingestion.strategies: {meeting: meeting-notes}` in
  `config.yaml` maps files tagged `meeting` to it (Ladder 1 on top of
  Ladder 2/3).

## Guidance for plugin authors

- **Loading is error-tolerant.** A plugin that fails to import, doesn't
  expose the declared attribute, or violates a conformance invariant is
  skipped. The skip carries a warning; it never takes the server down or
  breaks the built-ins. Fix the warning; don't rely on silent tolerance.
- **Be honest in `detect()`.** Return low confidence on weak evidence. A
  greedy `detect()` steals documents from the strategy that actually fits;
  the printed detection line makes this auditable after the fact.
- **Respect the token budget.** Oversized chunks degrade embedding quality
  and violate the conformance suite. Split, don't truncate.
- **Keep metadata small and JSON-safe.** Metadata is stored per chunk;
  put provenance there, not payloads.
- **Never set `qa_context`.** It is reserved for the `transcript` strategy
  and gated by the conformance suite.
- **Test with the conformance suite.** Run your strategy through the same
  invariant checks the built-ins pass before you ship the entry point.

## Why `retrieval.qa_context_in_fts` defaults to false

Transcript chunks carry `qa_context` (the question, participants, exchange
id). Including that text in the shared full-text index is **opt-in**
(`retrieval.qa_context_in_fts: true`). The reason: Postgres `ts_rank` has no
IDF. Question phrasing repeats across nearly every exchange. So question
text in the shared FTS index systematically skews ranking toward transcript
boilerplate. Keep it off until you have evaluated retrieval quality on your
own corpus with it on. The `qa_context` columns and GIN index are available
either way for structured access.

---

## Pick settings with data, not guesswork

Chunking and retrieval settings interact. The calibrate sweep measures
query-time recipes on your own golden labels and writes the winner to a
lock file. The step-by-step loop is [VALIDATION.md](VALIDATION.md).

---

## Next steps

- [Ingestion pipeline](INGESTION.md) — what happens after `build_chunks()`
- [Getting started (research)](getting-started.md) — ingesting transcripts end to end
- [Validation runbook](VALIDATION.md) — calibrate then lock, step by step
- [API reference](API.md) — `source_type` on the ingest routes

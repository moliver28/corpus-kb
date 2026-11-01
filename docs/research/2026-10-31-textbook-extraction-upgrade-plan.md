# Textbook Extraction Upgrade — Program Plan (2026-10-31)

Status: APPROVED (plan v2, post-falsification pass). Base: master @ 92fe7d4.
Concurrency: a separate quality session is running PR waves from `Corpus-kb-w1-*` worktrees
(#239 naming, #230 schema-parity nameset, #233 store-writer dedup, #215 fixture diagnostics).
No quality branch touches `textbook*` files. Shared-file watchlist: `pyproject.toml` +
`quality-baselines.json` (#233), `config.py`/`config.yaml` (#239), `schema_parity_gate.py` (#230).
This program touches `pyproject.toml` only in W3-A (new extra) and re-checks the quality
session's merge state before every push.

## Goal

Upgrade textbook extraction quality and performance with zero regression, no default-behavior
change without a flag, and a code area that ends more elegant and easier to maintain than it
started. Desk-research findings were verified at file:line and then adversarially falsified;
the surviving design is recorded here.

## Verified ground truth @ 92fe7d4

1. **Adapter discards structure** — `textbook_pages.py` `items_from_convert_result` keeps only
   label+text; level, bbox, multi-provenance, and tables (`TableItem.export_to_markdown()`)
   are dropped.
2. **Cache never validates bounds** — `textbook_cache.py` `load_window_cache` checks
   parser/sha/window-index but reads `start_page`/`end_page` without comparing them to the
   requested window bounds; a stale/foreign record can serve wrong pages.
3. **Static parser tags** — `PARSER_VERSIONS = {"docling": "docling-v1", "pypdf": "pypdf-v1"}`
   never bumps when the adapter logic changes, so stale cache entries survive adapter changes.
4. **First-title-only attribution** — packed chunks record `metadata.section` = the FIRST
   block's title only.
5. **Split attribution lie** — `_bounded_pieces` splits of an oversized block each get the
   FULL block page range, not the pages they actually contain.

## Elegance contract (binding for every wave)

- One typed item contract (rich `PageItem`, optional fields, legacy shape preserved).
- One CacheKey helper owning filename + record match (single place).
- One `_make_chunk` helper in `textbook_assemble.py` (dedupes the two PlannedChunk sites).
- One `WindowOutcome` type per parsed window (record + parser + cache hit + warnings).
- Canonical/enriched separation: default envelope byte-identical; all new data behind the
  `textbook.structured_output` flag or conditional envelope keys.
- Every touched file ends ≤250 lines (`textbook.py` is 273 today — waves must shrink it);
  `files_over_250` ceiling never grows; never edit `quality-baselines.json`.
- No `type: ignore`, no `Any` where a real type works, absolute imports,
  `from __future__ import annotations` everywhere.

## Waves

### W0 — Golden bench + real baseline (merges FIRST)

Owns: `src/corpus_kb/eval/textbook_bench.py` (new), `tests/test_textbook_bench.py` (new),
`eval/golden/manifest.json` at repo root `eval/` if package layout demands, baseline doc.
- Deterministic bench: page-text fidelity vs a pypdfium2 text layer, section F1 vs PDF outline
  bookmarks, chunk stats (count/tokens/budget conformance), **page-attribution exactness**
  (claimed page_start/page_end must equal the true span, reconstructable from blocks), wall-clock.
- Golden set: manifest pins ≥3 openly licensed PDFs (OpenStax CC-BY preferred) + `--books-dir`
  / `TEXTBOOK_GOLDEN_DIR` support for the user's local books; PDFs not committed
  (.gitignore `eval/golden/pdfs/`), fetch via `python -m corpus_kb.eval.golden_fetch` with
  SHA256 pinning. Timebox: if downloads fail, ship local-dir support + document.
- Baseline doc `docs/research/textbook-baseline-2026-10-31.md`: pypdf-path numbers (required)
  and docling-path numbers (if the venv probe succeeds), CUDA probe verdict (gates W3-A),
  machine specs, honest limitations.
- Docling venv prep `.venv-docling` (worktree-local, disposable): validates the `docling>=2.4+`
  extra installs on Windows/py3.11; CUDA probe via torch inside it.
- Commit: `feat: Add textbook golden bench and record the pre-change baseline`
- Gate note: bench is in-package → ruff/pyright/xenon/coverage apply; keep metrics pure and
  the driver thin.

### W1-A — Cache correctness + parse outcomes (default envelope byte-identical)

Owns: `textbook_cache.py`, `textbook.py` (outcome plumbing; ends ≤250 lines),
`contracts/envelopes.py`, parity registration, tests.
- `load_window_cache` validates requested bounds: record start/end must lie within the
  requested (start, end) and every cached page inside [start, end]; any mismatch = miss.
- One `CacheKey` helper owning filename + record match.
- `WindowOutcome` (new module to keep textbook.py ≤250): record + parser_used + cache_hit +
  warnings. `_parse_windows` returns outcomes, not bare records.
- Conditional `textbook` envelope key (TEXTBOOK_KEYS frozenset; #222 TITLE_KEYS precedent),
  registered registry-first (envelopes → parity test → handler attach in run_pipeline → `poe gen`).
- Tests: oob/stale/corrupt cache records miss; outcome plumbing; default envelope byte-identity.
- Commit: `feat: Validate textbook window cache bounds and surface parse outcomes`

### W1-B — Rich page items (default-off)

Owns: `textbook_pages.py`, `textbook_subprocess.py` (serialization if needed),
`textbook_settings.py`, both `config.yaml` copies, textbook tests. Does NOT touch
`textbook.py`/`textbook_cache.py` (W1-A's files) — zero-overlap split.
- `PageItem` keeps positional (page, label, text); adds keyword-only optionals
  (level, table_markdown, bbox) defaulting None; legacy shape preserved.
- Adapter populates level/bbox always (free metadata from the docling walk); table markdown
  export behind the new `textbook.structured_output` flag (default false).
- Parser tag bumps docling-v1 → docling-v2 (one-time intended cache invalidation); the rich
  variant caches under a distinct tag so a flag flip never silently degrades.
- Tests: adapter fakes, cache round trip with rich fields, tag variants, flag-off byte-identity.
- Commit: `feat: Capture rich structure in textbook page parse behind a flag`

### W2-A — True page attribution + one chunk builder

Owns: `textbook_assemble.py` + tests. Pre-check: quality-session issue #231 scope.
- One `_make_chunk` helper; packed chunk `heading_path=[section title]` (the column already
  persists end-to-end: `ingestion/base.py:164-178` maps it, `ingest_batch` INSERTs it).
- A single page map built once from blocks (per-item char offsets) so every chunk — packed or
  split — claims the pages it actually contains.
- Commit: `feat: Attribute textbook chunks to their true pages and section paths`

### W2-B — Citation channel (default-off), enrichment deleted

Pre-check: #246 merge state. Trace `search.qa_context_in_fts` first; citations ride chunk
metadata built from the W2-A page map + heading (e.g. "pp. 34-36 — Section 2.1"), exposed
through the existing metadata channel. Per-chunk embedding-input enrichment is architecturally
impossible in late mode (spans_provider embeds document spans) and is NOT attempted.
- Commit: `feat: Add optional textbook citation metadata to chunks`

### W2-C — Textbook profile YAML

Owns: `profiles/` YAML + loader data-type entry if needed. No code beyond loader wiring.
- Commit: `feat: Add a textbook ingestion profile`

### W3-A — PaddleOCR-VL challenger (gated)

Gates: W0's CUDA probe verdict + pyproject [ocr-vl] extra edit (approved with this plan).
New parser behind `textbook.parser: paddle-vl` (default stays docling); transformers route
(AutoModelForImageTextOrText... AutoModelForImageTextToText), subprocess-isolated, own parser
tag. Measured on the W0 bench vs docling baseline; report only — no default flip.
If CUDA absent: CPU feasibility numbers only, or descope with a written verdict.
- Commit: `feat: Add a PaddleOCR-VL textbook parser challenger`

### W3-B — Semantic units (audit-gated)

Audit first: next free migration number (postgres 039/duckdb 040 candidates), whether a unit
span table already exists, and whether chunk metadata suffices (no migration) before adding
schema. Schema changes must land in BOTH migration sets (schema parity gate) or be descoped
to metadata-only.
- Commit: `feat: Link textbook chunks to semantic units` (scope per audit)

### W4 — Closeout

Worker-reuse perf decision driven by W0/W3 bench numbers (implement only if it wins on the
bench and keeps the subprocess isolation contract); calibrate sweep run on a textbook corpus
post-merge; final report: before/after bench, parser comparison, PRs, follow-up issues.

## Error register (top risks → mitigations)

| Risk | Mitigation |
|---|---|
| Cross-file contention W1-A/W1-B | Strict file ownership split; rebase before every push |
| Quality session merges collide on shared files | Re-check `gh pr list` + rebase before every push |
| docling wheel fails on Windows py3.11 | W0 venv probe reports verdict; WebSearch after 2 failures |
| PaddleOCR-VL transformers route ≠ docs | Code against installed-API verification, fakes in tests |
| Cache tag bump invalidates user caches | Intended, documented in baseline doc |
| Envelope key leaks to non-textbook sources | Conditional attach; parity test byte-identity asserted |
| Coverage floor (87.5) — new untested code | Bench metrics are pure + unit-tested; driver thin |
| files_over_250 grows | textbook.py must END ≤250; new files ≤250 |
| Bench flakiness (network/timing) | Wall-clock separate from correctness metrics; downloads timeboxed |
| mimosa pre-push deep audit spans worktrees | Orchestrator-only pushes; fix-first loop |

## Protocol

- Builders: commit-only in `Corpus-kb-tb-w*`; never push/close/merge.
- Orchestrator: local gates (`poe verify`, `poe validation-gate`) in the worktree → push →
  poll CI every 20 min → momus review → fix-first loop → squash-merge → rebase next wave.
- Every brief embeds: search to increase doubt (verify every assumption at file:line before
  implementing) and WebSearch after 2 failed fix attempts.
- Branches: `feature/textbook-<slug>` (hyphens only); single-line conventional commits.
- Off-peak: 2026-10-31 is a Saturday — off-peak all day; heavy waves run now.

## Verification (program done when)

- Every PR: local `poe verify` + `poe validation-gate` green in the worktree; remote CI green
  on all workflows; momus findings fixed or answered.
- End state: all PRs merged, master green, bench report shows ≥ baseline fidelity with true
  page attribution, `files_over_250` ≤ 81, coverage ≥ 87.5, dup ≤ 1.6, no new warnings.

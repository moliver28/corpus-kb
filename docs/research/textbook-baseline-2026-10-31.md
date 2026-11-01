# Textbook Extraction — Pre-Change Baseline (2026-10-31)

Program: [textbook-extraction-upgrade](2026-10-31-textbook-extraction-upgrade-plan.md),
wave W0. Base commit: `92fe7d4`. Branch: `feature/textbook-golden-bench`.

This document records the real, measured pre-change baseline of the
textbook strategy, plus the W0 probes (docling venv, CUDA) that gate
later waves. Every number below comes from
`corpus_kb/eval/textbook_bench.py` runs against the pinned golden books;
nothing is estimated.

## Golden set

Manifest: `src/corpus_kb/eval/textbook_golden.json`. Three OpenStax
books, all CC-BY-4.0, downloaded from OpenStax's production CDN
(`d3bxy9euw4e147.cloudfront.net/oscms-prodcms`), sha256-pinned:

| Book | File | Pages | Bookmarks | Size |
|---|---|---|---|---|
| Concepts of Biology | `ConceptsOfBiology-LR.pdf` | 638 | 158 | 24 MB |
| Introductory Statistics | `IntroductoryStatistics-OP.pdf` | 863 | 164 | 30 MB |
| Chemistry 2e | `Chemistry2e-OP.pdf` | 1331 | 204 | 213 MB |

Fetch: `python -m corpus_kb.eval.golden_fetch` (downloads verify each
sha256). Extra local books ride in through the bench's `--books-dir`
flag; the golden directory itself can be moved with
`CORPUS_KB_TEXTBOOK_GOLDEN_DIR`. A fourth candidate, U.S. History
(`USHistory-OP.pdf`), was dropped: the served file lacks a PDF EOF
marker and pypdf cannot open it, so the production gate would reject it.

## Metric definitions (as implemented)

- **Page-text fidelity** — mean over the text layer's pages of the
  character Levenshtein distance between the parser's page text and an
  independent text layer, each squashed to single spaces first,
  divided by the longer length. The layer is the gold scope: a page it
  covers that the parser returned nothing for scores 1.0, so dropping
  pages cannot inflate the mean (rule corrected post-review; the first
  published table averaged over the intersection only). The layer is
  pypdfium2 5.14 (exact, independent of both parsers).
- **Section F1** — set F1 between detected section titles (casefolded,
  whitespace-collapsed) and the PDF outline bookmark titles in the
  parsed span.
- **Chunk stats** — count, mean/max `estimate_tokens` (~4 chars/token),
  and the share of chunks within the production budget target (1638
  estimated tokens = 6553 chars).
- **Page-attribution exactness** — the document text rebuilt from page
  texts with the assembly join rule; a chunk passes when its claimed
  `page_start`/`page_end` equals the pages its char range actually
  covers. `join_matches_document_text` true on every run below, so the
  rebuild and the assembler agree byte for byte.
- **Parse wall-clock** — `time.perf_counter` around the window loop
  only, into a fresh temporary cache directory, so it never mixes with
  the correctness metrics.

## Machine

| Part | Value |
|---|---|
| CPU | Intel Core i5-12600K (12th gen) |
| RAM | 32 GB |
| GPU | NVIDIA GeForce RTX 3080, 12288 MiB, driver 617.14 |
| OS | Windows 11 (build 26200) |
| Bench venv | Python 3.11.5 (`.venv`, uv), pypdf 6.21.4, pypdfium2 5.14.0 |
| Docling venv | Python 3.12 (`.venv-docling`, uv), docling 2.145.0, pypdf 6.21.4, torch 2.15.0+cpu |

## Probe verdicts

### Docling venv (gates W1/W3 parser work) — PASS, with two caveats

`docling>=2.144` + `pypdf>=6.21` install and import on Windows under
Python 3.12 created by `uv`. `DocumentConverter` imports and converts a
one-page PDF. Caveats:

1. The system `python` on PATH is 3.15, and docling's `pyclipper`
   dependency has no 3.15 wheel (source build fails without MSVC). The
   probe venv must be created on 3.11-3.13 (`uv venv --python 3.12`).
2. Docling 2.145 splits its dependency set behind `docling-slim`;
   `pypdfium2>=4.30,<6` arrives transitively through
   `docling-slim[standard]`. pypdfium2 is therefore **not** a direct or
   base dependency of corpus-kb: the bench guards the import and falls
   back to pypdf, and the baseline doc records which layer ran.

### CUDA (gates W3-A PaddleOCR-VL) — GPU PRESENT, venv torch is CPU-build

`nvidia-smi` reports the RTX 3080 (12 GB) with driver 617.14, so the
hardware gate for W3-A passes. The docling probe venv installed
`torch 2.15.0+cpu` (uv's Windows default index), so `torch.cuda` is
unavailable inside that particular venv. W3-A will need CUDA wheels
(`--index-url https://download.pytorch.org/whl/cu121` or the uv
equivalent) in its own extra; the GPU itself is not the blocker.

## Baseline numbers — pypdf parser (required)

Run: `python -m corpus_kb.eval.textbook_bench --parser pypdf`, full
books, window size 50, fresh cache per book, text layer pypdfium2.

| Metric | Concepts of Biology (638p) | Introductory Statistics (863p) | Chemistry 2e (1331p) |
|---|---|---|---|
| Parse wall-clock (s) | 9.9 | 15.0 | 27.4 |
| Compared pages (layer scope) | 638 | 863 | 1331 |
| Fidelity: mean normalized distance | 0.002995 | 0.006789 | 0.007985 |
| Pages missing from parser output | 1 | 1 | 2 |
| Sections: detected / gold titles | 115 / 138 | 140 / 152 | 173 / 184 |
| Section precision / recall / F1 | 1.000 / 0.833 / 0.909 | 1.000 / 0.921 / 0.959 | 1.000 / 0.940 / 0.969 |
| Chunks | 371 | 370 | 580 |
| Mean / max estimated tokens | 1129.0 / 1633 | 1143.1 / 1638 | 1153.2 / 1630 |
| Budget conformance | 1.000 | 1.000 | 1.000 |
| Attribution exact rate | 0.0997 (37/371) | 0.1757 (65/370) | 0.0862 (50/580) |
| Rebuild equals document text | yes | yes | yes |

Fidelity rows are layer-scope means under the corrected rule; each
book's blank-or-unextracted page(s) score 1.0 and are listed
explicitly. Section, chunk, and attribution numbers are
rule-independent and stand from the first run batch.

Reading: the pypdf parser tracks the embedded text stream almost
exactly (fidelity 0.3-0.8 percent under the layer-scope rule), recovers
structure purely from
bookmarks (precision 1.0 by construction — every detected title comes
from `read_outline` filling a page with no header item), stays inside
the token budget everywhere, and — the headline finding — attributes
only 8.6-17.6 percent of chunks to their true pages. The rest are
split pieces of oversized blocks that claim the full block span while
covering a subset of its pages: plan ground truth #5, and the defect
wave W2-A exists to close. Packed multi-block chunks also inherit the
first-block page start, so long sections carry extra phantom pages in
both directions.

## Baseline numbers — docling parser (venv-gated)

Run: same CLI under `.venv-docling` (Python 3.12), `--parser docling`,
Concepts of Biology capped to the first 100 pages (2 windows of 50) —
a timeboxed probe of the smallest book, per the W0 plan.

| Metric | Concepts of Biology, pages 1-100 |
|---|---|
| Parse wall-clock (s) | 357.1 (CPU torch, 2 windows; in-process converter) |
| Compared pages (of parsed) | 95 / 95 |
| Fidelity: mean normalized distance | 0.171339 |
| Sections: detected / gold titles | 146 / 23 |
| Section precision / recall / F1 | 0.00685 / 0.04348 / 0.01183 |
| Chunks | 46 |
| Mean / max estimated tokens | 1251.7 / 1599 |
| Budget conformance | 1.000 |
| Attribution exact rate | 0.9565 (44/46) |
| Rebuild equals document text | yes |

Reading: docling is roughly 30x slower per page on CPU (357 s / 100
pages vs 12.4 s / 638 pages for pypdf), its reading order differs from
the raw text stream (fidelity 0.17 — reflow, hyphenation, and table
re-layout move characters), and it detects 146 header items — real
structure signal pypdf has zero of. The section F1 is near zero only
because the metric's match rule is exact normalized title equality,
and Docling's header strings rarely equal the bookmark strings
verbatim (numbering, trailing art labels). Treat the docling F1 as a
floor, not a structure verdict; a fuzzy matcher is future work for the
bench, not for W0. Attribution exactness is 95.7 percent because
Docling's frequent headers keep blocks small enough to rarely split;
the two failures are still split pieces claiming whole-block spans.

## Run notes

- Every run used a fresh temporary window cache, so the parse
  wall-clock is a true parse; `parse_seconds` covers the window loop
  only (import, text layer, and metric compute are excluded by
  design).
- End-to-end wall-clock per book on this machine: roughly 7-20 minutes
  for the pypdf runs (dominated by interpreter start, the unstructured
  import chain, and the exact Levenshtein DP on the few pages whose
  squashed text differs), and about 9 minutes for the docling run
  (dominated by the 357 s parse plus torch/RapidOCR startup).
- The docling run emitted docling-internal warnings (RapidOCR "text
  detection result is empty" on cover-art pages); none of them are
  corpus-kb warnings, and the bench captured only corpus-kb warnings.

## What the baseline already proves

1. **The split-piece attribution defect is real and measurable.** On
   the 60-page smoke of Concepts of Biology, 35 chunks carried a
   0.0857 attribution exact rate: one oversized block (pages 1-6,
   ~16.6k chars) split into four pieces, and every piece claimed the
   full block span while covering only its own pages. This is plan
   ground truth #5, and it is what wave W2-A closes.
2. **Digital-native books flatter native-text parsers.** pypdf's page
   text and the pypdfium2 layer squash to byte-identical strings on
   ordinary body pages, so pypdf fidelity near 0 does not mean "the
   text layer is weak" — it means both extractors read the same
   embedded text stream. Scanned or image-heavy books would separate
   the two immediately.
3. **Bookmarks dominate pypdf structure.** pypdf items carry no labels,
   so every detected section title comes from `read_outline` filling
   pages with no header item; section F1 measures bookmark coverage
   plus assembly carry, not parser structure detection.

## Honest limitations

- The text-layer gold is semi-independent for the pypdf parser only in
  the fallback case; on this machine pypdfium2 was present, so both
  parsers were scored against the same independent extractor. Still,
  both gold and parser read the same digital-native text stream — the
  fidelity metric cannot see scan-vs-text quality gaps on these books.
- Outline bookmarks are the structure gold. A book with a poor or
  missing outline would score F1 near zero regardless of parse quality.
- Windows-local quirk (documented for reproducibility): Git Bash's
  `msys-magic-1.dll` sits on PATH and `python-magic`'s loader finds it
  before its bundled `libmagic.dll`, which deadlocks CPython at
  `import magic` (unstructured → python-magic). Prepending
  `.venv/Lib/site-packages/magic/libmagic` to PATH fixes local runs.
  This is a shell-environment issue, not a repo defect; CI (ubuntu,
  no MSYS on PATH) is unaffected.
- `CORPUS_KB_TEXTBOOK_GOLDEN_DIR` is an eval-harness path, not a config
  override; `corpus-kb doctor` lists it as a set-but-unmapped
  `CORPUS_KB_*` variable because it is not in
  `KNOWN_CORPUS_KB_ENV_VARS` (`config.py:276`). Registering it there is
  a one-line follow-up for a wave that owns `config.py` (W1 sessions do).

## Reproduce

```bash
python -m corpus_kb.eval.golden_fetch
python -m corpus_kb.eval.textbook_bench --golden --parser pypdf
# docling (probe venv):
uv venv --python 3.12 .venv-docling
uv pip install --python .venv-docling/Scripts/python.exe "docling>=2.144" "pypdf>=6.21" httpx
uv pip install --python .venv-docling/Scripts/python.exe -e . --no-deps
.venv-docling/Scripts/python.exe -m corpus_kb.eval.textbook_bench --golden --parser docling
```

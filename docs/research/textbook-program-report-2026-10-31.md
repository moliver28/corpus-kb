# Textbook extraction upgrade program — report (2026-10-31)

Program window: 2026-10-31 → 2026-11-01. Plan:
[2026-10-31-textbook-extraction-upgrade-plan.md](2026-10-31-textbook-extraction-upgrade-plan.md).
Eight waves shipped as PRs #262–#269. Follow-ups: #270, #271.

## Headline outcome

Attribution exact rate on the golden bench moved from 0.0997 / 0.1757 / 0.0862
to **1.0000** (371 / 370 / 580 chunks). Every chunk now names the true book
pages it came from.

## Results

Golden bench: 3 OpenStax CC-BY books, pypdf path.

| Book | Attribution before → after | Fidelity | Sections F1 | Chunks |
|------|---------------------------|----------|-------------|--------|
| Anatomy and Physiology | 0.0997 → 1.0000 | 0.0030 | 0.909 | 371 |
| Introduction to Sociology 3e | 0.1757 → 1.0000 | 0.0068 | 0.959 | 370 |
| U.S. History | 0.0862 → 1.0000 | 0.0080 | 0.969 | 580 |

Fidelity uses the corrected rule: an unparsed layer page scores 1.0. Chunk
counts and budget conformance (1.000) are identical before and after — zero
collateral change.

## What changed

| Wave | PR | Change |
|------|-----|--------|
| W0 | #262 | Golden bench runner + recorded pre-change baseline |
| W1-A | #263 | CacheKey exact-span lookups; WindowOutcome envelope |
| W1-B | #264 | Rich PlannedChunk items: heading path, citation + units metadata keys |
| W2-A | #265 | True page spans on chunks (the attribution win) |
| W2-B | #266 | Optional citations metadata behind the `citations` flag |
| W2-C | #267 | Authored textbook ingestion profile |
| W3-A | #268 | PaddleOCR-VL parser seam behind the optional `[ocr-vl]` extra |
| W3-B | #269 | Table units on chunks over table pages (`structured_output`) |

## Probe verdicts

- PaddleOCR-VL on CPU fails: dense pages never reach end-of-sequence, 4096-token cap, more than 25 minutes per page. Default stays docling; `paddle-vl` is an opt-in seam only.
- Docling on CPU parses 100 pages in 357 s; model load dominates. Persistent worker reuse is the throughput lever — tracked as #270.
- Citation and units metadata does not round-trip to search results — tracked as #271.

## Limits and next touches

- `textbook.py` sits at exactly 250 lines. Next touch: extract the
  window-cache plumbing (plan noted in the wave docs).
- Calibrate sweep on a textbook corpus is deferred (needs Ollama + live ingestion).
- The pypdf path carries the attribution win; the docling layer page rule
  (unparsed layer page = 1.0) is documented in the bench runner.

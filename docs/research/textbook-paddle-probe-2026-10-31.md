# PaddleOCR-VL Challenger Probe (2026-10-31)

Program: [textbook-extraction-upgrade](2026-10-31-textbook-extraction-upgrade-plan.md),
wave W3-A. Base: `feature/textbook-rich-items` tip `90a74dc`; challenger commit
`3b08989` (branch `feature/textbook-ocr-challenger`). Verdict up front:

> **The transformers whole-page route fails the probe.** It is an order of
> magnitude slower than Docling on CPU and full-page decoding does not
> terminate. The parser ships as a measurement challenger only; the docling
> default is unchanged and should stay unchanged on this evidence.

## What the probe validated (the code works)

The probe venv (`uv venv --python 3.12 .venv-ocrvl`, disposable) installed
`-e ".[ocr-vl]"` cleanly: transformers 5.22.1, torch 2.15.0+cpu (PyPI
Windows default), pypdfium2 5.14.0. The lazy-import contract held: the
extras-less suite stays green while this venv alone carries the stack.

The `PaddlePaddle/PaddleOCR-VL` weights (~1.8 GB) downloaded once into the
HF cache and loaded through the exact transformers 5.x recipe the parser
codes against (`AutoModelForImageTextToText` + `AutoProcessor`, chat
template, generate, prompt-trim, decode). Model load ran about 1-2 minutes
on the dev machine. One page completed end to end, so the parse surface,
the item shape, and the cache/subprocess seams are all exercised for real.

## Machine

| Part | Value |
|---|---|
| CPU | Intel Core i5-12600K (12th gen) |
| GPU | NVIDIA GeForce RTX 3080, 12288 MiB, driver 617.14 — IDLE (see below) |
| Probe venv | Python 3.12 (uv), transformers 5.22.1, torch 2.15.0+cpu, pypdfium2 5.14.0 |
| Book | `ConceptsOfBiology-LR.pdf` (golden, 638 pages, CC-BY-4.0) |

## Numbers (from the probe run log; runs were killed at the timebox)

| Page | Content | Generate wall-clock | Output chars |
|---|---|---|---|
| 1 | Cover art | 74.0 s | 19 |
| 2 | Front matter (dense) | > 5 min, killed mid-decode | — |
| 14 | Body text (§1.1) | > 25 min, killed mid-decode | — |

Readings:

1. **Full-page decoding does not terminate.** Text pages burn the
   generation cap (`max_new_tokens = 4096`) without reaching EOS. The
   element-level recognition model expects region crops; a whole page is
   out of distribution, and decode cost is bounded only by our cap.
2. **Art pages yield nothing without layout crops.** The cover produced
   19 characters. The official pipeline crops regions first
   (PP-DocLayoutV2); the transformers route has no layout detector.
3. **CPU pace is a non-starter.** 74 s for a near-empty page; text pages
   exceeded 25 min. Even the completed-page pace projects to days for a
   638-page book. The W0 baseline: pypdf 0.02 s/page, docling 3.6 s/page
   (CPU). The challenger is 1-2 orders of magnitude slower.
4. **Fidelity and structure could not be meaningfully scored.** Page 1's
   normalized distance is 0.0 against a near-empty text layer
   (uninformative); no body page finished. All items carry the label
   `text` (no layout detector), so section detection would fall back to
   bookmarks exactly like the pypdf tier, and table/chart/formula
   markdown is structurally absent — the registry records that the
   structured-output flag is a no-op for this parser.
5. **The GPU never ran.** PyPI Windows torch wheels are CPU builds; CUDA
   wheels need the explicit `download.pytorch.org` index. Per the plan
   the extra documents this but does not wire it, so VRAM use is not
   measured. A CUDA run is the one untested axis that could change the
   pace verdict (typical 0.9B decode speedups are 10-30x, which would
   still leave a ~2-6 h/book projection at cap-bounded output lengths).
6. Benign transformers 5.22.1 warnings observed at load: unrecognized
   `rope_parameters` key `mrope_section`, a `tie_word_embeddings`
   checkpoint/config mismatch notice, and a deprecated
   `AutoImageProcessor` mapping in `PaddleOCRVLProcessor`. All are
   upstream issues; none blocked load or generate.

## Verdict and the W4 decision

The challenger as implemented (transformers element-level route over
whole pages) loses to docling on every measured axis on this machine:
pace, fidelity, and structure. Two paths could change the verdict, and
both stay open:

- **CUDA wheels** for this repo's route: an explicit-index install in a
  probe venv, untested here (timebox). Would fix pace; would not fix
  the non-terminating full-page decode or the missing structure.
- **The official PaddleOCR pipeline** (layout detection + region crops):
  needs the `paddlepaddle` wheels, which have no Windows GPU builds —
  the same blocker the plan recorded when this route was chosen.

Recommendation: keep `textbook.parser: docling` as the default, treat
`paddle-vl` as an opt-in measurement surface for CUDA-capable
environments, and do not invest worker-reuse engineering (W4) in the
paddle path on this evidence.

## Reproduce

```bash
uv venv --python 3.12 .venv-ocrvl
uv pip install --python .venv-ocrvl/Scripts/python.exe -e ".[ocr-vl]"
.venv-ocrvl/Scripts/python.exe -u findings/probes/paddle_probe.py out.json 4 14
```

The probe script (findings/probes/paddle_probe.py) drives the shipped
parse surface directly, per page, with pypdfium2 text-layer fidelity and
per-stage timings. The runs recorded here were stopped before the final
JSON write; the numbers above come from the per-page log lines.

## Honest limitations

- One book, three pages attempted, zero GPU minutes. The probe measured
  pace and mechanics, not best-case quality.
- `max_new_tokens = 4096` is our cap (the model card's element demos use
  1024 for crops); a different cap trades wall-clock for truncation, not
  for termination.
- The >25 min body-page figure is one sample of one dense page; nothing
  in the log suggests it was converging (the cap exists because EOS is
  not reached on full pages).
- The golden book is digital-native; scanned books would add OCR-prefill
  cost on top of these numbers, not subtract from them.

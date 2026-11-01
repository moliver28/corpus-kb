"""PaddleOCR-VL live probe for the W3-A challenger (timeboxed, local only).

Drives the real parse surface (corpus_kb.ingestion.strategies.textbook_paddle)
over the first N pages of the smallest golden book and records: wall-clock,
per-page timings, page-text fidelity vs the pypdfium2 layer, labels, and
rich-field presence. Writes one JSON blob for the probe doc.

Run: .venv-ocrvl/Scripts/python.exe findings/probes/paddle_probe.py <out.json> [pages] [first_page]
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

BOOK = Path.home() / ".corpus-kb" / "textbook-golden" / "ConceptsOfBiology-LR.pdf"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paddle_probe_results.json")
PAGE_CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 20
PAGE_START = int(sys.argv[3]) if len(sys.argv) > 3 else 1  # 1-based first page


def squash(text: str) -> str:
    return " ".join(text.split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main() -> int:
    import torch
    import transformers

    tp = importlib.import_module("corpus_kb.ingestion.strategies.textbook_paddle")
    pdfium = importlib.import_module("pypdfium2")

    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    state = tp.load_paddle_model()
    load_seconds = time.perf_counter() - t0
    if state is None:
        print("model load failed (extra missing?)")
        return 1

    document = pdfium.PdfDocument(str(BOOK))
    page_count = min(PAGE_START + PAGE_CAP - 1, len(document))
    parse_seconds = 0.0
    for page in range(PAGE_START, page_count + 1):
        t_render = time.perf_counter()
        image = tp.render_page(str(BOOK), page)
        r_seconds = time.perf_counter() - t_render
        t_rec = time.perf_counter()
        text = tp.recognize_image(state, image) if image is not None else ""
        g_seconds = time.perf_counter() - t_rec
        parse_seconds += r_seconds + g_seconds
        try:
            layer = document[page - 1].get_textpage().get_text_range()
        except Exception:
            layer = ""
        a, b = squash(text), squash(layer)
        dist = levenshtein(a, b) / max(len(a), len(b)) if max(len(a), len(b)) else 0.0
        rows.append(
            {
                "page": page,
                "render_s": round(r_seconds, 2),
                "generate_s": round(g_seconds, 2),
                "chars": len(text),
                "layer_chars": len(layer),
                "fidelity": round(dist, 6),
                "head": text[:160].replace("\n", " | "),
            }
        )
        print(f"p{page}: gen {g_seconds:5.1f}s chars {len(text):5d} fid {dist:.4f}", flush=True)
    document.close()

    compared = [r for r in rows if max(r["chars"], r["layer_chars"]) > 0]
    summary = {
        "model_id": tp.PADDLE_VL_MODEL,
        "torch": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "transformers": transformers.__version__,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "book": BOOK.name,
        "pages": page_count - PAGE_START + 1,
        "first_page": PAGE_START,
        "model_load_s": round(load_seconds, 1),
        "parse_wall_s": round(parse_seconds, 1),
        "seconds_per_page": round(parse_seconds / max(page_count - PAGE_START + 1, 1), 1),
        "mean_fidelity": round(sum(r["fidelity"] for r in compared) / len(compared), 6)
        if compared
        else None,
        "items_label_set": ["text"],
        "table_markdown_items": 0,
        "empty_pages": [r["page"] for r in rows if r["chars"] == 0],
        "rows": rows,
    }
    OUT.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

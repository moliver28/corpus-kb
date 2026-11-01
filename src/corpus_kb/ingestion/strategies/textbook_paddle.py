"""The PaddleOCR-VL parse surface: the ``[ocr-vl]`` challenger.

The transformers route from the program plan (W3-A):
``AutoModelForImageTextToText`` plus ``AutoProcessor`` against
``PaddlePaddle/PaddleOCR-VL``. The class pair and the inference recipe
are verified against the transformers 5.x model docs; native
``PaddleOCRVLForConditionalGeneration`` support needs
``transformers>=5.0``, which is the extra's floor.

The transformers route recognizes ONE image per call (the ``OCR:``
prompt). A window therefore renders each page through pypdfium2 and
returns one ``text`` item per page. The official page pipeline (layout
detection, per-region table/formula/chart prompts) needs the
paddlepaddle wheels, which have no Windows GPU builds, so this parser
is text-only today: rich fields stay ``None`` and the structured-output
flag does not change its output (it is not in
``PARSER_STRUCTURED_VARIANTS``).

Every third-party import is lazy (the ``eval/_ranx.py`` pattern):
nothing in this module's import chain imports transformers, torch, or
pypdfium2 at module level, so an extras-less CI imports the strategy
cleanly. Tests fake the model state and patch the loader seams.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .textbook_docling import page_row
from .textbook_pages import PageItem
from .textbook_settings import TextbookSettings
from .textbook_subprocess import parse_window_in_subprocess

logger = logging.getLogger(__name__)

#: The Hugging Face model id (verified: huggingface.co/PaddlePaddle/PaddleOCR-VL).
PADDLE_VL_MODEL = "PaddlePaddle/PaddleOCR-VL"

#: The element-level text prompt (the model card's text-recognition task).
_OCR_PROMPT = "OCR:"

#: Generation cap per page. A dense page runs a few thousand tokens; the
#: cap bounds a runaway decode so the window timeout stays meaningful.
_MAX_NEW_TOKENS = 4096

#: Page render scale over the 72 dpi PDF base (2.0 = 144 dpi).
_RENDER_SCALE = 2.0

_MISSING_EXTRA = "textbook strategy needs the optional 'ocr-vl' extra; used pypdf page text"


@dataclass(frozen=True)
class PaddleState:
    """The loaded model pair. The fields stay opaque (fakes substitute)."""

    model: Any
    processor: Any


def load_paddle_model() -> PaddleState | None:
    """The model and processor, or None when the ``[ocr-vl]`` extra is absent.

    Lazy import on purpose: CI never installs the extra, so the import
    edges stay invisible to static checks. The model lands on CUDA when
    the torch build sees it, else on CPU, in bfloat16 only where CUDA
    can run it. First call downloads the weights (~2 GB).
    """
    try:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
    except ImportError:
        return None
    device = "cuda" if bool(torch.cuda.is_available()) else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model = transformers.AutoModelForImageTextToText.from_pretrained(PADDLE_VL_MODEL, dtype=dtype)
    model.to(device)
    model.eval()
    processor = transformers.AutoProcessor.from_pretrained(PADDLE_VL_MODEL)
    return PaddleState(model=model, processor=processor)


def render_page(pdf_path: str, page: int) -> Any | None:
    """One page as a PIL image, or None when the render fails.

    A render gap drops that one page; :func:`parse_window_paddle`
    records the gap as a window warning (the pypdf per-page rule).
    """
    try:
        pdfium = importlib.import_module("pypdfium2")
        document = pdfium.PdfDocument(pdf_path)
        try:
            bitmap = document[page - 1].render(scale=_RENDER_SCALE)
            return bitmap.to_pil()
        finally:
            document.close()
    except Exception:
        return None


def recognize_image(state: PaddleState, image: Any) -> str:
    """One image through the model: the recognized text, or ``""``.

    The recipe is the transformers 5.x PaddleOCR-VL example: one chat
    message, the processor's chat template, one generate call, and a
    decode that skips the special tokens. A decode that returns no
    string comes back as ``""``.
    """
    messages = [
        {
            "role": "user",
            "content": [{"type": "image", "image": image}, {"type": "text", "text": _OCR_PROMPT}],
        }
    ]
    inputs = state.processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(state.model.device)
    prompt_len = len(inputs["input_ids"][0])
    generated = state.model.generate(**inputs, max_new_tokens=_MAX_NEW_TOKENS)
    trimmed = [out[prompt_len:] for out in generated]
    text = state.processor.batch_decode(
        trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
    )[0]
    return text if isinstance(text, str) else ""


def parse_window_paddle(
    state: PaddleState,
    path: Path,
    start: int,
    end: int,
    warnings: list[str],
    structured: bool = False,
) -> list[PageItem]:
    """One window through PaddleOCR-VL: one ``text`` item per parsed page.

    ``structured`` is accepted for seam symmetry and is a no-op today:
    the transformers route has no layout detector, so there are no
    table or formula regions to export. A page whose render fails is a
    gap: its number lands in one warning per window (the pypdf rule),
    so a shrunk record never caches silently. A page whose recognition
    comes back empty drops, like pypdf's empty pages.
    """
    del structured
    items: list[PageItem] = []
    failed: list[int] = []
    for page in range(start, end + 1):
        image = render_page(str(path), page)
        if image is None:
            failed.append(page)
            continue
        text = recognize_image(state, image)
        if text.strip():
            items.append(PageItem(page, "text", text))
    if failed:
        skipped = ", ".join(str(page) for page in failed)
        warnings.append(f"pages {skipped} did not render; skipped")
    return items


def paddle_window_rows(
    pdf_path: str, start: int, end: int, structured: bool = False
) -> list[dict[str, object]]:
    """Child-side rows: load the model, parse the window, serialize.

    The child calls this (the same shape as the docling child target).
    A missing extra raises, so the child exits nonzero and the parent
    takes the pypdf path. The row payload has no warning channel, so a
    child-side render gap logs here (the child's stderr reaches the
    run's log) instead of traveling to the envelope.
    """
    state = load_paddle_model()
    if state is None:
        raise RuntimeError("the 'ocr-vl' extra is missing in the child process")
    window_warnings: list[str] = []
    items = parse_window_paddle(state, Path(pdf_path), start, end, window_warnings, structured)
    for note in window_warnings:
        logger.warning("paddle-vl child window pages %s-%s: %s", start, end, note)
    return [page_row(item) for item in items]


def confirm_paddle(
    settings: TextbookSettings,
    state: PaddleState | None,
    warnings: list[str],
) -> tuple[bool, PaddleState | None]:
    """The one-time extra check: (paddle active, model state or None).

    Subprocess mode probes the module spec only: the child imports
    transformers and loads the model, and the parent never does (the
    docling parent-import rule, issue #220). torch is probed beside
    transformers: transformers without torch would pass a lone probe
    and then crash every child window.
    """
    del state
    loaded: PaddleState | None = None
    if settings.window_subprocess:
        active = find_spec("transformers") is not None and find_spec("torch") is not None
    else:
        loaded = load_paddle_model()
        active = loaded is not None
    if not active:
        warnings.append(_MISSING_EXTRA)
    return active, loaded


def parse_one_paddle_window(
    settings: TextbookSettings,
    state: PaddleState | None,
    path: Path,
    start: int,
    end: int,
    index: int,
    warnings: list[str],
) -> list[PageItem] | None:
    """One paddle window; ``None`` hands the window to the pypdf fallback.

    Subprocess mode mirrors the docling wrapper: a crash or timeout
    warns once and returns ``None``. In-process mode needs the loaded
    model; a failed parse warns once and returns ``None``.
    """
    if settings.window_subprocess:
        try:
            items = parse_window_in_subprocess(
                path,
                "paddle-vl",
                start,
                end,
                settings.window_timeout_s,
                settings.structured_output,
            )
        except Exception:
            items = None  # a wrapper failure must not kill the run
        if items is None:
            warnings.append(
                f"Window {index + 1} crashed or timed out in the child process. "
                "Used the pypdf fallback."
            )
        return items
    if state is None:
        return None
    try:
        return parse_window_paddle(state, path, start, end, warnings, settings.structured_output)
    except Exception:
        warnings.append(f"paddle-vl failed on pages {start} to {end}; used pypdf page text")
        return None

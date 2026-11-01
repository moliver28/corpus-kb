"""Tests for the PaddleOCR-VL parser surface (the ``[ocr-vl]`` challenger).

Everything runs against fakes: the model state is a pure-python double
that answers the exact calls the transformers recipe makes, the render
seam returns sentinel images, and no test downloads a model or imports
transformers. The lazy-import guard runs the package import in a fresh
``python -I`` child and fails if transformers, torch, or pypdfium2
enter ``sys.modules`` at import time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from corpus_kb.ingestion.strategies import textbook_paddle, textbook_subprocess
from corpus_kb.ingestion.strategies.textbook_paddle import (
    PADDLE_VL_MODEL,
    PaddleState,
    confirm_paddle,
    paddle_window_rows,
    parse_one_paddle_window,
    parse_window_paddle,
    recognize_image,
)
from corpus_kb.ingestion.strategies.textbook_pages import PageItem
from corpus_kb.ingestion.strategies.textbook_registry import (
    _PARSER_SEAMS,
    PARSER_STRUCTURED_VARIANTS,
    PARSER_VERSIONS,
    parser_key,
)
from corpus_kb.ingestion.strategies.textbook_settings import TextbookSettings, settings_from

# ---------------------------------------------------------------------------
# Fakes: the model pair answers the exact recipe calls
# ---------------------------------------------------------------------------


class _FakeInputs(dict):
    """BatchFeature double: a real mapping, ``.to()`` returns self."""

    def __init__(self, prompt_tokens: int) -> None:
        super().__init__()
        self.prompt_tokens = prompt_tokens
        self.to_device = False
        # A dict subclass on purpose: the recipe calls generate(**inputs),
        # which needs a real mapping unpack.
        self["input_ids"] = [[101] * prompt_tokens]

    def to(self, device: object) -> _FakeInputs:
        self.to_device = True
        return self


class _FakeModel:
    """The generate half: records the inputs, emits prompt + new tokens."""

    def __init__(self) -> None:
        self.device = "cpu"
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> list[list[int]]:
        self.calls.append(kwargs)
        input_ids = kwargs["input_ids"]
        assert isinstance(input_ids, list) and isinstance(input_ids[0], list)
        return [[101] * len(input_ids[0]) + [42, 43]]


class _FakeProcessor:
    """The processor half: checks the message shape, decodes the tail."""

    def __init__(self) -> None:
        self.decode_kwargs: dict[str, object] = {}

    def apply_chat_template(self, messages: object, **kwargs: object) -> _FakeInputs:
        self.messages = messages
        self.template_kwargs = kwargs
        return _FakeInputs(prompt_tokens=5)

    def batch_decode(self, rows: object, **kwargs: object) -> list[object]:
        self.decode_kwargs = kwargs
        self.decoded_rows = rows
        return ["page body text"]


def _fake_state() -> tuple[PaddleState, _FakeModel, _FakeProcessor]:
    model = _FakeModel()
    processor = _FakeProcessor()
    return PaddleState(model=model, processor=processor), model, processor


# ---------------------------------------------------------------------------
# recognize_image: the recipe shape
# ---------------------------------------------------------------------------


def test_recognize_sends_the_ocr_prompt_and_trims_the_prompt() -> None:
    state, model, processor = _fake_state()
    text = recognize_image(state, "sentinel-image")
    assert text == "page body text"
    message = processor.messages[0]
    assert message["role"] == "user"
    content = message["content"]
    assert content[0] == {"type": "image", "image": "sentinel-image"}
    assert content[1]["type"] == "text"
    assert content[1]["text"] == "OCR:"
    assert processor.template_kwargs["add_generation_prompt"] is True
    assert processor.template_kwargs["return_dict"] is True
    assert model.calls and model.calls[0]["max_new_tokens"] == 4096
    generated = processor.decoded_rows
    assert generated == [[42, 43]]  # the 5 prompt tokens were trimmed away
    assert processor.decode_kwargs["skip_special_tokens"] is True


def test_recognize_returns_empty_when_decode_gives_no_string() -> None:
    class _NoneDecode(_FakeProcessor):
        def batch_decode(self, rows: object, **kwargs: object) -> list[object]:
            return [None]

    state = PaddleState(model=_FakeModel(), processor=_NoneDecode())
    assert recognize_image(state, "sentinel-image") == ""


# ---------------------------------------------------------------------------
# parse_window_paddle: per-page items, dropped pages, flag no-op
# ---------------------------------------------------------------------------


def test_window_builds_one_text_item_per_rendered_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "render_page", lambda path, page: f"img-{page}")
    warnings: list[str] = []
    items = parse_window_paddle(state, Path("book.pdf"), 2, 4, warnings)
    assert items == [
        PageItem(2, "text", "page body text"),
        PageItem(3, "text", "page body text"),
        PageItem(4, "text", "page body text"),
    ]
    assert warnings == []


def test_window_drops_a_failed_render_and_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _model, _processor = _fake_state()

    def _render(path: str, page: int) -> object | None:
        return None if page == 3 else f"img-{page}"

    calls: list[int] = []

    def _decode(rows: object, **kwargs: object) -> list[str]:
        calls.append(1)
        return ["   "] if len(calls) == 1 else ["body"]  # page 2 decodes blank

    monkeypatch.setattr(textbook_paddle, "render_page", _render)
    state.processor.batch_decode = _decode
    warnings: list[str] = []
    items = parse_window_paddle(state, Path("book.pdf"), 2, 4, warnings)
    assert [item.page for item in items] == [4]
    # The render gap warns (the pypdf per-page rule); a blank decode does not.
    assert warnings == ["pages 3 did not render; skipped"]


def test_window_names_every_failed_render_in_one_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "render_page", lambda path, page: None)
    warnings: list[str] = []
    items = parse_window_paddle(state, Path("book.pdf"), 2, 4, warnings)
    assert items == []
    assert warnings == ["pages 2, 3, 4 did not render; skipped"]


def test_structured_flag_is_a_no_op_for_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "render_page", lambda path, page: f"img-{page}")
    plain = parse_window_paddle(state, Path("book.pdf"), 1, 2, [], structured=False)
    rich = parse_window_paddle(state, Path("book.pdf"), 1, 2, [], structured=True)
    assert rich == plain
    assert all(item.table_markdown is None and item.level is None for item in rich)


def test_render_page_returns_none_without_the_extra() -> None:
    # pypdfium2 is absent in CI, so the lazy import raises and the helper
    # answers None (the documented render-gap rule).
    assert textbook_paddle.render_page(str(Path("no-such.pdf")), 1) is None


# ---------------------------------------------------------------------------
# Registry and cache tags
# ---------------------------------------------------------------------------


def test_paddle_tag_has_no_rich_variant() -> None:
    assert parser_key("paddle-vl", False) == "paddle-vl-v1"
    assert parser_key("paddle-vl", True) == "paddle-vl-v1"
    assert "paddle-vl" not in PARSER_STRUCTURED_VARIANTS


def test_docling_and_unknown_tags_keep_their_shape() -> None:
    assert parser_key("docling", True) == "docling-v2-rich"
    assert parser_key("docling", False) == "docling-v2"
    assert parser_key("not-a-parser", True) == "docling-v2-rich"  # docling fallback
    assert parser_key("not-a-parser", False) == "docling-v2"


def test_seam_registry_covers_every_optional_parser() -> None:
    assert set(_PARSER_SEAMS) == set(PARSER_VERSIONS) - {"pypdf"}
    assert PADDLE_VL_MODEL == "PaddlePaddle/PaddleOCR-VL"


# ---------------------------------------------------------------------------
# confirm_paddle: the one-time extra check
# ---------------------------------------------------------------------------


def _settings(subprocess: bool) -> TextbookSettings:
    return settings_from({"textbook": {"window_subprocess": subprocess}})


def test_confirm_probes_the_spec_in_subprocess_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(textbook_paddle, "find_spec", lambda name: object())
    warnings: list[str] = []
    active, state = confirm_paddle(_settings(True), None, warnings)
    assert active is True
    assert state is None  # the parent never loads the model
    assert warnings == []


def test_confirm_probes_torch_beside_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = {"transformers": True, "torch": False}

    def _find_spec(name: str) -> object | None:
        return object() if specs.get(name) else None

    monkeypatch.setattr(textbook_paddle, "find_spec", _find_spec)
    warnings: list[str] = []
    active, state = confirm_paddle(_settings(True), None, warnings)
    # transformers without torch must yield the one missing-extra warning,
    # not a crash warning on every child window.
    assert active is False
    assert state is None
    assert len(warnings) == 1 and "ocr-vl" in warnings[0]


def test_confirm_warns_without_the_extra_in_subprocess_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(textbook_paddle, "find_spec", lambda name: None)
    warnings: list[str] = []
    active, state = confirm_paddle(_settings(True), None, warnings)
    assert active is False
    assert state is None
    assert len(warnings) == 1 and "ocr-vl" in warnings[0]


def test_confirm_loads_the_model_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "load_paddle_model", lambda: state)
    warnings: list[str] = []
    active, loaded = confirm_paddle(_settings(False), None, warnings)
    assert active is True
    assert loaded is state
    assert warnings == []


def test_confirm_warns_when_the_model_fails_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(textbook_paddle, "load_paddle_model", lambda: None)
    warnings: list[str] = []
    active, loaded = confirm_paddle(_settings(False), None, warnings)
    assert active is False
    assert loaded is None
    assert len(warnings) == 1 and "ocr-vl" in warnings[0]


# ---------------------------------------------------------------------------
# parse_one_paddle_window: the seam the orchestrator calls
# ---------------------------------------------------------------------------


_SUB = "corpus_kb.ingestion.strategies.textbook_paddle.parse_window_in_subprocess"


def test_one_window_subprocess_success(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _wrapper(
        pdf: Path, parser: str, start: int, end: int, timeout_s: float, structured: bool = False
    ) -> list[PageItem]:
        seen.update(parser=parser, start=start, end=end, structured=structured)
        return [PageItem(1, "text", "shipped")]

    monkeypatch.setattr(_SUB, _wrapper)
    settings = _settings(True)
    items = parse_one_paddle_window(settings, None, Path("b.pdf"), 1, 2, 0, [])
    assert items == [PageItem(1, "text", "shipped")]
    assert seen == {"parser": "paddle-vl", "start": 1, "end": 2, "structured": False}


def test_one_window_subprocess_crash_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_SUB, lambda *args: None)
    settings = _settings(True)
    warnings: list[str] = []
    items = parse_one_paddle_window(settings, None, Path("b.pdf"), 1, 2, 0, warnings)
    assert items is None
    assert warnings and "crashed or timed out" in warnings[0]


def test_one_window_in_process_uses_the_loaded_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "render_page", lambda path, page: f"img-{page}")
    settings = _settings(False)
    items = parse_one_paddle_window(settings, state, Path("b.pdf"), 1, 1, 0, [])
    assert items == [PageItem(1, "text", "page body text")]


def test_one_window_in_process_failure_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _model, _processor = _fake_state()

    def _boom(*args: object) -> list[PageItem]:
        raise RuntimeError("oom")

    monkeypatch.setattr(textbook_paddle, "parse_window_paddle", _boom)
    settings = _settings(False)
    warnings: list[str] = []
    items = parse_one_paddle_window(settings, state, Path("b.pdf"), 3, 4, 0, warnings)
    assert items is None
    assert warnings == ["paddle-vl failed on pages 3 to 4; used pypdf page text"]


def test_one_window_in_process_without_a_state_returns_none() -> None:
    settings = _settings(False)
    warnings: list[str] = []
    items = parse_one_paddle_window(settings, None, Path("b.pdf"), 1, 1, 0, warnings)
    assert items is None
    assert warnings == []


# ---------------------------------------------------------------------------
# paddle_window_rows: the child-side payload
# ---------------------------------------------------------------------------


def test_rows_serialize_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    state, _model, _processor = _fake_state()
    monkeypatch.setattr(textbook_paddle, "load_paddle_model", lambda: state)
    monkeypatch.setattr(textbook_paddle, "render_page", lambda path, page: f"img-{page}")
    rows = paddle_window_rows("book.pdf", 1, 2)
    assert rows == [
        {"page": 1, "label": "text", "text": "page body text"},
        {"page": 2, "label": "text", "text": "page body text"},
    ]


def test_rows_raise_without_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(textbook_paddle, "load_paddle_model", lambda: None)
    with pytest.raises(RuntimeError, match="ocr-vl"):
        paddle_window_rows("book.pdf", 1, 2)


# ---------------------------------------------------------------------------
# The child target routes by parser name
# ---------------------------------------------------------------------------


class _FakeQueue:
    """Payload-queue double: records the shipped rows."""

    def __init__(self) -> None:
        self.rows: list[object] | None = None

    def put(self, rows: list[object]) -> None:
        self.rows = rows


def test_child_target_routes_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _rows(pdf: str, start: int, end: int, structured: bool = False) -> list[dict[str, object]]:
        seen.update(pdf=pdf, start=start, end=end, structured=structured)
        return [{"page": start, "label": "text", "text": "child body"}]

    monkeypatch.setattr(textbook_paddle, "paddle_window_rows", _rows)
    queue = _FakeQueue()
    textbook_subprocess._window_child("b.pdf", "paddle-vl", 1, 2, queue)
    assert queue.rows == [{"page": 1, "label": "text", "text": "child body"}]
    assert seen == {"pdf": "b.pdf", "start": 1, "end": 2, "structured": False}


def test_child_target_routes_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Prov:
        page_no = 1

    class _Node:
        label = "text"
        text = "child body"

        def __init__(self) -> None:
            self.prov = [_Prov()]

    class _Document:
        def iterate_items(self) -> list[tuple[_Node, int]]:
            return [(_Node(), 0)]

    class _Result:
        def __init__(self) -> None:
            self.document = _Document()

    class _Converter:
        def convert(self, source: str, page_range: tuple[int, int] | None = None) -> _Result:
            assert page_range == (1, 2)
            return _Result()

    class _Module:
        DocumentConverter = _Converter

    class _FakeImportlib:
        def import_module(self, name: str) -> type[_Module]:
            assert name == "docling.document_converter"
            return _Module()

    monkeypatch.setattr(textbook_subprocess, "importlib", _FakeImportlib())
    queue = _FakeQueue()
    textbook_subprocess._window_child("b.pdf", "docling", 1, 2, queue)
    # The docling walk provides the level, so the row carries it (W1-B).
    assert queue.rows == [{"page": 1, "label": "text", "text": "child body", "level": 0}]


def test_child_target_raises_on_an_unknown_parser() -> None:
    queue = _FakeQueue()
    with pytest.raises(ValueError, match="no child rows factory"):
        textbook_subprocess._window_child("b.pdf", "tesseract", 1, 2, queue)
    assert queue.rows is None  # nothing shipped under a foreign name


def test_child_factories_mirror_the_seam_registry() -> None:
    # Every registry parser with a child must have exactly one rows
    # factory, so no parser can silently borrow another parser's child.
    assert set(textbook_subprocess._WINDOW_ROW_FACTORIES) == set(_PARSER_SEAMS)


# ---------------------------------------------------------------------------
# Lazy-import guard: a transformers-less env imports the module cleanly
# ---------------------------------------------------------------------------


def test_import_never_pulls_transformers_torch_or_pdfium() -> None:
    code = (
        "import sys;"
        "import corpus_kb.ingestion.strategies.textbook_paddle;"
        "import corpus_kb.ingestion.strategies.textbook;"
        "bad = {'transformers', 'torch', 'pypdfium2'} & set(sys.modules);"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    # The guard covers the whole strategy package surface.


def test_parse_one_paddle_window_subprocess_crash_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A crash or timeout in the child warns once and hands the window
    # to the pypdf fallback.
    def _boom(*args: object, **kwargs: object) -> list[PageItem]:
        raise RuntimeError("child died")

    monkeypatch.setattr(textbook_paddle, "parse_window_in_subprocess", _boom)
    notes: list[str] = []
    settings = TextbookSettings(window_subprocess=True)
    items = parse_one_paddle_window(settings, None, Path("b.pdf"), 1, 3, 0, notes)
    assert items is None
    expected = "Window 1 crashed or timed out in the child process. Used the pypdf fallback."
    assert notes == [expected]


def test_paddle_window_rows_logs_child_gaps(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A child-side render gap logs in the child (stderr reaches the run
    # log) instead of traveling to the envelope.

    def _gap_parse(
        state: object,
        path: object,
        start: int,
        end: int,
        notes_list: list[str],
        structured: bool,
    ) -> list[PageItem]:
        notes_list.append("render gap on page 3")
        return []

    monkeypatch.setattr(textbook_paddle, "load_paddle_model", lambda: "state")
    monkeypatch.setattr(textbook_paddle, "parse_window_paddle", _gap_parse)
    rows = paddle_window_rows("b.pdf", 3, 4)
    assert rows == []
    assert any("child window pages 3-4" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# load_paddle_model / render_page: the lazy-import seams under fake modules
# ---------------------------------------------------------------------------


class _FakeCuda:
    """The torch.cuda seam: no GPU, so the load lands on CPU."""

    def is_available(self) -> bool:
        return False


class _FakeTorch:
    """The torch double: the two dtype selection inputs."""

    def __init__(self) -> None:
        self.cuda = _FakeCuda()
        self.float32 = "float32"
        self.bfloat16 = "bfloat16"


class _FakeLoadedModel:
    """The from_pretrained product: records the move and eval order."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def to(self, device: str) -> None:
        self.events.append(f"to:{device}")

    def eval(self) -> None:
        self.events.append("eval")


class _FakeAutoModel:
    """The AutoModelForImageTextToText seam: records the load call."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.model = _FakeLoadedModel(events)
        self.load_name = ""
        self.load_kwargs: dict[str, object] = {}

    def from_pretrained(self, name: str, **kwargs: object) -> _FakeLoadedModel:
        self.events.append("load")
        self.load_name = name
        self.load_kwargs = dict(kwargs)
        return self.model


class _FakeAutoProcessor:
    """The AutoProcessor seam: hands back one sentinel processor."""

    def __init__(self) -> None:
        self.processor = object()
        self.load_name = ""

    def from_pretrained(self, name: str) -> object:
        self.load_name = name
        return self.processor


class _FakeTransformers:
    """The transformers module double: the two auto classes."""

    def __init__(self, events: list[str]) -> None:
        self.AutoModelForImageTextToText = _FakeAutoModel(events)
        self.AutoProcessor = _FakeAutoProcessor()


class _FakeLoadImportlib:
    """The import seam for the loader: torch plus transformers."""

    def __init__(self, events: list[str]) -> None:
        self.torch = _FakeTorch()
        self.transformers = _FakeTransformers(events)

    def import_module(self, name: str) -> object:
        if name == "torch":
            return self.torch
        assert name == "transformers"
        return self.transformers


def test_load_paddle_model_loads_the_pair_on_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    fake = _FakeLoadImportlib(events)
    monkeypatch.setattr(textbook_paddle, "importlib", fake)
    auto_model = fake.transformers.AutoModelForImageTextToText
    auto_processor = fake.transformers.AutoProcessor
    state = textbook_paddle.load_paddle_model()
    assert isinstance(state, PaddleState)
    assert state.model is auto_model.model
    assert state.processor is auto_processor.processor
    # No CUDA in the fake: float32, load, move to CPU, then eval, in order.
    assert events == ["load", "to:cpu", "eval"]
    assert auto_model.load_name == PADDLE_VL_MODEL
    assert auto_model.load_kwargs == {"dtype": "float32"}
    assert auto_processor.load_name == PADDLE_VL_MODEL


def test_load_paddle_model_returns_none_without_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoTorch:
        def import_module(self, name: str) -> object:
            raise ImportError(name)

    monkeypatch.setattr(textbook_paddle, "importlib", _NoTorch())
    assert textbook_paddle.load_paddle_model() is None


class _FakeBitmap:
    """The render product: ``to_pil`` hands back one sentinel image."""

    def __init__(self) -> None:
        self.image = object()

    def to_pil(self) -> object:
        return self.image


class _FakePdfPage:
    """The page double: records the render scale."""

    def __init__(self, bitmap: _FakeBitmap) -> None:
        self.bitmap = bitmap
        self.render_scale: float | None = None

    def render(self, scale: float) -> _FakeBitmap:
        self.render_scale = scale
        return self.bitmap


class _FakePdfDocument:
    """The document double: zero-based indexing plus one close."""

    def __init__(self) -> None:
        self.page = _FakePdfPage(_FakeBitmap())
        self.opened_with = ""
        self.index: int | None = None
        self.closes = 0

    def __getitem__(self, index: int) -> _FakePdfPage:
        self.index = index
        return self.page

    def close(self) -> None:
        self.closes += 1


class _FakePdfium:
    """The pypdfium2 module double."""

    def __init__(self) -> None:
        self.document = _FakePdfDocument()

    def PdfDocument(self, path: str) -> _FakePdfDocument:  # noqa: N802 (pypdfium2 API)
        self.document.opened_with = path
        return self.document


class _FakeRenderImportlib:
    """The import seam for render_page: pypdfium2 only."""

    def __init__(self, pdfium: _FakePdfium) -> None:
        self.pdfium = pdfium

    def import_module(self, name: str) -> object:
        assert name == "pypdfium2"
        return self.pdfium


def test_render_page_renders_one_page_and_closes_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfium = _FakePdfium()
    monkeypatch.setattr(textbook_paddle, "importlib", _FakeRenderImportlib(pdfium))
    document = pdfium.document
    image = textbook_paddle.render_page("book.pdf", 3)
    assert image is document.page.bitmap.image
    assert document.opened_with == "book.pdf"
    assert document.index == 2  # page - 1: the zero-based PDF index
    assert document.page.render_scale == 2.0
    assert document.closes == 1


def test_render_page_returns_none_when_the_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoPdfium:
        def import_module(self, name: str) -> object:
            raise ImportError(name)

    monkeypatch.setattr(textbook_paddle, "importlib", _NoPdfium())
    assert textbook_paddle.render_page("book.pdf", 1) is None

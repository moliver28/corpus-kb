"""The method-advisor panel: view model, strings, and the page render.

The advisor NEVER re-ranks: it calls Agent A's ``rank_methods`` (the
one rubric) over the real register file, so the UI shows the same rows
the selector op returns. The page render runs against a fake NiceGUI
namespace (the ``UiNamespace`` Protocol), so no extra is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.ui import advisor_vm

# ---------------------------------------------------------------------------
# The view model over the ONE rubric
# ---------------------------------------------------------------------------


async def test_empty_task_answers_empty_with_a_note() -> None:
    view = await advisor_vm.method_advisor("   ")
    assert view["status"] == "empty"
    assert str(view["note"])
    assert view["recommendations"] == []


async def test_success_ranks_the_real_register() -> None:
    view = await advisor_vm.method_advisor("Split interview chunks into themes")
    assert view["status"] == "success"
    assert view["note"] == ""
    rows = view["recommendations"]
    assert rows, "the shipped register must rank at least one method"
    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        for key in (
            "name",
            "rank_reason",
            "validation_required",
            "permitted_claims",
            "do_not_claim",
            "missing_inputs",
        ):
            assert key in row


async def test_ranking_is_deterministic() -> None:
    first = await advisor_vm.method_advisor("Count spelling mistakes per document")
    second = await advisor_vm.method_advisor("Count spelling mistakes per document")
    assert [row["name"] for row in first["recommendations"]] == [
        row["name"] for row in second["recommendations"]
    ]


async def test_unknown_output_family_answers_invalid_with_the_vocab() -> None:
    view = await advisor_vm.method_advisor("Rank methods", output_family="pet_pictures")
    assert view["status"] == "invalid"
    assert "output_family" in str(view["note"])
    options = view["options"]
    assert "labels" in options["output_families"]
    assert "seed_labels" in options["input_kinds"]
    assert "baseline" in options["complexity_tiers"]


async def test_unknown_complexity_tier_answers_invalid() -> None:
    view = await advisor_vm.method_advisor("Rank methods", max_complexity_tier="wizard")
    assert view["status"] == "invalid"
    assert "max_complexity_tier" in str(view["note"])


async def test_missing_register_answers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(config: object = None) -> Any:
        raise ValueError("Analysis method register not found: config/analysis_methods.yaml.")

    monkeypatch.setattr(advisor_vm, "load_method_register", broken)
    view = await advisor_vm.method_advisor("Rank methods")
    assert view["status"] == "unavailable"
    assert "not found" in str(view["note"])


async def test_input_constraints_shape_the_ranking() -> None:
    base = await advisor_vm.method_advisor("Rank methods")
    capped = await advisor_vm.method_advisor("Rank methods", max_complexity_tier="baseline")
    assert len(capped["recommendations"]) <= len(base["recommendations"])


def test_advisor_markdown_carries_the_honest_rows() -> None:
    view = {
        "status": "success",
        "note": "",
        "recommendations": [
            {
                "rank": 1,
                "name": "BERTopic",
                "summary": "Cluster texts into themes.",
                "rank_reason": "Matches the themes family.",
                "missing_inputs": [],
                "validation_required": "Read two topics by hand.",
                "permitted_claims": "Say the corpus holds these themes.",
                "do_not_claim": "Do not claim the themes are exhaustive.",
            }
        ],
    }
    text = advisor_vm.advisor_markdown(view)
    assert "BERTopic" in text
    assert "Do not claim the themes are exhaustive." in text
    assert "Read two topics by hand." in text


def test_advisor_markdown_shows_the_honest_note_on_failure() -> None:
    text = advisor_vm.advisor_markdown(
        {"status": "empty", "note": "Describe the analysis task first.", "recommendations": []}
    )
    assert "Describe the analysis task first." in text


# ---------------------------------------------------------------------------
# The page render through a fake NiceGUI namespace
# ---------------------------------------------------------------------------


class _FakeInput:
    def __init__(self, label: str = "", **kwargs: Any) -> None:
        self.label = label
        self.value = ""


class _FakeContainer:
    def __init__(self, page: _FakeUi) -> None:
        self._page = page

    def clear(self) -> None:
        self._page.markdowns = []

    def __enter__(self) -> _FakeContainer:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class _FakeUi:
    """Records every element call; pages run against the Protocol."""

    def __init__(self) -> None:
        self.pages: dict[str, Any] = {}
        self.labels: list[str] = []
        self.markdowns: list[str] = []
        self.buttons: dict[str, Any] = {}
        self.inputs: list[_FakeInput] = []
        self.notifies: list[str] = []

    def page(self, path: str) -> Any:
        def deco(handler: Any) -> Any:
            self.pages[path] = handler
            return handler

        return deco

    def label(self, text: str = "", **kwargs: Any) -> object:
        self.labels.append(text)
        return object()

    def markdown(self, content: str = "", **kwargs: Any) -> object:
        self.markdowns.append(content)
        return object()

    def button(self, text: str = "", on_click: Any = None, **kwargs: Any) -> object:
        self.buttons[text] = on_click
        return object()

    def input(self, label: str = "", **kwargs: Any) -> _FakeInput:
        box = _FakeInput(label)
        self.inputs.append(box)
        return box

    def column(self, **kwargs: Any) -> _FakeContainer:
        return _FakeContainer(self)

    def notify(self, message: str = "", **kwargs: Any) -> object:
        self.notifies.append(message)
        return object()

    def linear_progress(self, value: float = 0.0, **kwargs: Any) -> object:
        return object()

    def chat_message(self, text: str = "", sent_by_user: bool = False, **kwargs: Any) -> object:
        return object()

    def upload(self, **kwargs: Any) -> object:
        return object()


async def test_ask_page_renders_the_advisor_panel() -> None:
    from corpus_kb.ui import pages

    ui = _FakeUi()
    pages.register_pages(ui)
    assert "/ask" in ui.pages
    await ui.pages["/ask"]()
    assert "Method advisor" in ui.labels

    task_box = next(box for box in ui.inputs if "task" in box.label.lower())
    task_box.value = "Split interview chunks into themes"
    advisor_button = ui.buttons["Rank methods"]
    assert advisor_button is not None
    await advisor_button()
    joined = "\n".join(ui.markdowns)
    assert "rank" in joined.lower()


def test_split_csv_parses_and_drops_empties() -> None:
    assert advisor_vm.split_csv("raw_text, embeddings ,, ") == ["raw_text", "embeddings"]
    assert advisor_vm.split_csv("  ") is None

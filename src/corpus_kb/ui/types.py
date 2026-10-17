"""The pinned NiceGUI surface + UI context (UX program Wave 3).

Protocols ONLY — this module never imports nicegui. It pins the small
element surface the four pages use, so pyright (and extras-less CI)
type-checks the pages without the package. The real import happens in
``app.py`` behind the lazy seam, exactly like ``topics/engine.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, TypeVar

import asyncpg

_HandlerT = TypeVar("_HandlerT", bound="Callable[..., object]")


class UploadContent(Protocol):
    def read(self) -> bytes: ...


class UploadEvent(Protocol):
    content: UploadContent
    name: str


class UiInput(Protocol):
    value: str


class UiProgress(Protocol):
    value: float


class UiContainer(Protocol):
    def clear(self) -> None: ...
    def __enter__(self) -> UiContainer: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class UiNamespace(Protocol):
    """The ``nicegui.ui`` namespace surface the four pages use."""

    def page(self, path: str) -> Callable[[_HandlerT], _HandlerT]: ...
    def label(self, text: str = "", **kwargs: object) -> object: ...
    def markdown(self, content: str = "", **kwargs: object) -> object: ...
    def button(self, text: str = "", on_click: object = None, **kwargs: object) -> object: ...
    def input(self, label: str = "", **kwargs: object) -> UiInput: ...
    def upload(self, **kwargs: object) -> object: ...
    def linear_progress(self, value: float = 0.0, **kwargs: object) -> UiProgress: ...
    def chat_message(
        self, text: str = "", sent_by_user: bool = False, **kwargs: object
    ) -> object: ...
    def notify(self, message: str = "", **kwargs: object) -> object: ...
    def column(self, **kwargs: object) -> UiContainer: ...
    def run_with(self, app: object, **kwargs: object) -> None: ...


class NiceGuiModule(Protocol):
    """The lazy ``nicegui`` module surface (the ``ui`` namespace + run_with)."""

    ui: UiNamespace


@dataclass(frozen=True)
class UiContext:
    """What the pages need to call the tool layer."""

    config: dict[str, object]
    pool: asyncpg.Pool | None = None


_CONTEXT: UiContext | None = None


def set_ui_context(context: UiContext) -> None:
    """Server wiring calls this once at startup, before build_ui."""
    global _CONTEXT
    _CONTEXT = context


def get_ui_context() -> UiContext:
    """The current context, or an empty one (page handlers stay honest)."""
    return _CONTEXT or UiContext(config={})

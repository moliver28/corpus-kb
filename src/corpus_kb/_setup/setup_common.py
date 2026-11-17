"""Shared seam resolver for the setup split modules (issue #472).

The setup leaves resolve their collaborators (the confirmation prompt,
the pull steps, the extension check) through the install module at call
time. This keeps the ``install.<name>`` test patches working. A static
import of the install module would rebuild the old import cycle, so the
resolver below loads the module dynamically.

This module imports nothing from ``corpus_kb._setup``. Every setup
module may import from it without a cycle.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable
from typing import Protocol, cast


class InstallSurface(Protocol):
    """The install-module members the setup leaves resolve at call time."""

    def confirm(self, step: str) -> bool:
        """Ask the user to confirm one setup step."""
        ...

    def check_extensions(
        self, connection_string: str
    ) -> Awaitable[dict[str, tuple[bool, str]] | None]:
        """Probe the Postgres extensions that migrations 004/005 need."""
        ...

    def collect_model_consents(self) -> list[tuple[str, bool]]:
        """Ask every model-download consent question on one thread."""
        ...

    def setup_compose_pull(self, compose_cmd: str, apply: bool, agreed: bool | None = None) -> int:
        """Pull the compose stack images when confirmed."""
        ...

    def setup_pull_models(self, apply: bool, consents: list[tuple[str, bool]] | None = None) -> int:
        """Pull the Ollama models when confirmed."""
        ...

    def _verify_postgres_image(self, image: str, skip_verify: bool) -> None:
        """Verify the Postgres image signature unless skipped."""
        ...


def install_surface() -> InstallSurface:
    """Return the install module for the call-time patch seams."""
    return cast(InstallSurface, importlib.import_module("corpus_kb._setup.install"))

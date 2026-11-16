"""Concurrent pull pair for setup (zero-drift split of _setup/install.py).

The stdout router, the pre-download signature check, and the two
download stages (Docker images, Ollama models) that run side by side.
``install.py`` re-exports the pair runner and the check, so its
orchestrators and the ``install_module.*`` test patches keep working.

Patch seam: the tests patch ``corpus_kb._setup.install.confirm`` /
``install.setup_compose_pull`` / ``install.setup_pull_models`` /
``install._verify_postgres_image`` and drive setup end to end. Those
collaborators therefore resolve through the install module at CALL
time (the ``_*_fn`` helpers mirror ``setup_stack._confirm_fn``).
"""

from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable
from io import StringIO
from typing import TYPE_CHECKING, Any, TextIO

from corpus_kb._setup.install_state import save_install_state

if TYPE_CHECKING:
    from corpus_kb._setup.install import _SetupPlan


def _confirm_fn() -> Callable[[str], bool]:
    """Resolve ``confirm`` from the install module (test patch seam)."""
    from corpus_kb._setup import install

    return install.confirm


def _consents_fn() -> Callable[[], list[tuple[str, bool]]]:
    """Resolve ``collect_model_consents`` from the install module (patch seam)."""
    from corpus_kb._setup import install

    return install.collect_model_consents


def _compose_pull_fn() -> Callable[..., int]:
    """Resolve ``setup_compose_pull`` from the install module (patch seam)."""
    from corpus_kb._setup import install

    return install.setup_compose_pull


def _pull_models_fn() -> Callable[..., int]:
    """Resolve ``setup_pull_models`` from the install module (patch seam)."""
    from corpus_kb._setup import install

    return install.setup_pull_models


def _verify_image_fn() -> Callable[[str, bool], None]:
    """Resolve ``_verify_postgres_image`` from the install module (patch seam)."""
    from corpus_kb._setup import install

    return install._verify_postgres_image


class _StageRouter(io.TextIOBase):
    """A sys.stdout shim: routes each pull-pair thread's prints to its buffer.

    The two download stages share one process stdout. The router keeps
    their printed lines apart so the joined report stays ordered.
    """

    def __init__(self, routes: dict[int, StringIO], passthrough: TextIO) -> None:
        self._routes = routes
        self._passthrough = passthrough

    def write(self, text: str) -> int:
        sink = self._routes.get(threading.get_ident())
        if sink is None:
            return self._passthrough.write(text)
        return sink.write(text)

    def flush(self) -> None:
        sink = self._routes.get(threading.get_ident())
        if sink is None:
            self._passthrough.flush()
        else:
            sink.flush()


def _verify_image_before_pulls(plan: _SetupPlan, build_local: bool) -> bool:
    """Run the fatal signature check before any download starts.

    Returns True when setup may continue (verified, skipped, or not
    applicable) and False when verification failed and setup must stop.
    The check keeps its historical ``[Step 1/6]`` error prefix.
    """
    compose_pending = "compose" not in plan.completed and bool(plan.compose_cmd) and not build_local
    if not compose_pending:
        return True
    if plan.skip_image_verify:
        print("  Skipping Postgres image signature verification.")
        return True
    try:
        _verify_image_fn()(plan.postgres_image, False)
    except RuntimeError as exc:
        print(f"\n[Step 1/6] ERROR: {exc}")
        return False
    return True


def _pull_pair_stages(
    plan: _SetupPlan,
    *,
    build_local: bool,
    pull_possible: bool,
    pull_agreed: bool,
    consents: list[tuple[str, bool]],
    buffers: dict[str, StringIO],
    routes: dict[int, StringIO],
) -> tuple[Callable[[], int], Callable[[], int]]:
    """The two download stages: (image pull, model pull).

    Each stage registers its thread's stdout route first, so the
    _StageRouter keeps its printed lines inside its own buffer.
    """
    pull_pending = "pull" not in plan.completed
    models_pending = "models" not in plan.completed

    def _pull_stage() -> int:
        routes[threading.get_ident()] = buffers["pull"]
        if not pull_pending:
            print("\n[Step 1a/6] Docker images already pulled; skipping.")
            return 0
        print("\n[Step 1a/6] Pulling the Docker stack images...")
        compose_cmd = plan.compose_cmd
        if not pull_possible or compose_cmd is None:
            if build_local:
                print("  --build-local set: the image builds at compose up; pull skipped.")
            else:
                print("  Docker not found; skipping the image pull.")
            return 0
        return _compose_pull_fn()(compose_cmd, True, agreed=pull_agreed)

    def _models_stage() -> int:
        routes[threading.get_ident()] = buffers["models"]
        if not models_pending:
            print("\n[Step 5/6] Ollama models already pulled; skipping.")
            return 0
        print("\n[Step 5/6] Pulling the required Ollama models...")
        return _pull_models_fn()(True, consents=consents)

    return _pull_stage, _models_stage


def _run_pull_pair(plan: _SetupPlan, config: dict[str, Any], *, build_local: bool) -> int:
    """Pull Docker images and Ollama models concurrently, then report in order.

    The compose ``pull`` and the model downloads are the two slow network
    steps, so they run side by side in threads. Every confirmation is
    asked here on the calling thread first, so the prompts never race the
    downloads. Each stage's printed lines land in its own buffer, and the
    joined report prints pull first, models second — the same order every
    run. Each stage checkpoints on its own success, pull then models.
    """
    pull_pending = "pull" not in plan.completed
    models_pending = "models" not in plan.completed
    pull_possible = pull_pending and bool(plan.compose_cmd) and not build_local
    pull_agreed = False
    consents: list[tuple[str, bool]] = []
    if pull_possible:
        pull_agreed = _confirm_fn()("Pull the Docker images for the compose stack.")
    if models_pending:
        consents = _consents_fn()()

    buffers = {"pull": StringIO(), "models": StringIO()}
    routes: dict[int, StringIO] = {}
    results: dict[str, int] = {"pull": 0, "models": 0}
    labels = {"pull": "image pull", "models": "model pull"}
    pull_stage, models_stage = _pull_pair_stages(
        plan,
        build_local=build_local,
        pull_possible=pull_possible,
        pull_agreed=pull_agreed,
        consents=consents,
        buffers=buffers,
        routes=routes,
    )

    def _run_stage(name: str, stage: Callable[[], int]) -> None:
        try:
            results[name] = stage()
        except Exception as exc:
            results[name] = 1
            print(f"  ERROR: the {labels[name]} failed: {exc}")

    threads = (
        threading.Thread(target=_run_stage, args=("pull", pull_stage)),
        threading.Thread(target=_run_stage, args=("models", models_stage)),
    )
    real_stdout = sys.stdout
    sys.stdout = _StageRouter(routes, real_stdout)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.stdout = real_stdout

    print(buffers["pull"].getvalue(), end="")
    print(buffers["models"].getvalue(), end="")

    exit_code = results["pull"] | results["models"]
    progressed = False
    if pull_pending and pull_possible and not results["pull"]:
        plan.completed.append("pull")
        progressed = True
    if models_pending and not results["models"]:
        plan.completed.append("models")
        progressed = True
    if progressed:
        save_install_state(plan.state, config)
    return exit_code

"""The pull steps setup runs concurrently (W1 install ease).

``docker compose pull`` and the Ollama model pulls are the two slow
network steps of ``corpus-kb setup``, so the installer runs them side by
side. This module owns both pull steps and the consent collectors the
pair uses to ask every question on one thread before the downloads
start. The pair itself lives in ``install._run_pull_pair``; ``install``
re-exports the functions here so the historical ``install.<name>`` test
patch surface keeps working.

Patch seam: collaborators (``REPO_ROOT``, ``_run_cmd``) resolve through
the ``setup_stack`` module at CALL time, so tests patch them in one
place, exactly like the ``confirm`` seam.
"""

from __future__ import annotations

from corpus_kb._setup import setup_stack
from corpus_kb._setup.setup_steps import SETUP_REQUIRED_MODELS

#: Image pulls can move several GB; the default 300s cap is too tight.
IMAGE_PULL_TIMEOUT = 1800


def setup_compose_pull(compose_cmd: str, apply: bool, agreed: bool | None = None) -> int:
    """Pull the compose stack images (``docker compose pull``) when confirmed.

    ``agreed=None`` asks on the calling thread; a boolean consent (the
    concurrent pair collects it before the downloads start) skips the
    prompt.
    """
    compose_file = setup_stack.REPO_ROOT.parent / "compose.yaml"
    if not compose_file.exists():
        print("  ERROR: compose.yaml not found at repo root.")
        print(f"  Looked for: {compose_file}")
        return 1
    if not apply:
        return 0
    if agreed is None:
        agreed = setup_stack._confirm_fn()("Pull the Docker images for the compose stack.")
    if not agreed:
        print("  Skipped.")
        return 0
    parts = compose_cmd.split()
    cmd = [*parts, "-f", str(compose_file), "pull"]
    return setup_stack._run_cmd(cmd, cwd=setup_stack.REPO_ROOT.parent, timeout=IMAGE_PULL_TIMEOUT)


def collect_model_consents() -> list[tuple[str, bool]]:
    """Per required model: (model, agreed), asked in the declared order."""
    return [
        (model, setup_stack._confirm_fn()(f"Pull Ollama model '{model}'."))
        for model in SETUP_REQUIRED_MODELS
    ]


def pull_models_consents(consents: list[tuple[str, bool]]) -> int:
    """Pull each agreed model in order; a declined model prints its skip line."""
    exit_code = 0
    for model, agreed in consents:
        if not agreed:
            print(f"  Skipped {model}.")
            continue
        exit_code |= setup_stack._run_cmd(["ollama", "pull", model], timeout=600)
    return exit_code


def setup_pull_models(apply: bool, consents: list[tuple[str, bool]] | None = None) -> int:
    """Pull the models required by the default configuration.

    ``consents=None`` asks on the calling thread; the concurrent pair
    passes the consents it collected earlier so prompts never race the
    downloads. Dry runs and declined models keep the historical lines.
    """
    if not apply:
        for model in SETUP_REQUIRED_MODELS:
            print(f"  (dry run) ollama pull {model}")
        return 0
    if consents is None:
        consents = collect_model_consents()
    return pull_models_consents(consents)


__all__ = [
    "IMAGE_PULL_TIMEOUT",
    "collect_model_consents",
    "pull_models_consents",
    "setup_compose_pull",
    "setup_pull_models",
]

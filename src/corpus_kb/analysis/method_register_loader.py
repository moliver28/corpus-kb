"""Loader for the analysis method register.

Reads the register YAML (repo path first, packaged mirror next), validates
it against the models, and enforces the complete 01-30 id ledger. The
schema models live in ``method_register``; the routing checks live in
``method_register_routing``. One loader, many callers.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.settings import load_analysis_settings
from corpus_kb.errors import ValidationError

EXPECTED_IDS = frozenset(f"{index:02d}" for index in range(1, 31))


def _packaged_register_path(file_name: str) -> Path | None:
    """The packaged mirror inside the installed ``corpus_kb`` package."""
    ref = resources.files("corpus_kb") / "config" / file_name
    if not ref.is_file():
        return None
    with resources.as_file(ref) as path:
        return path


def load_method_register(config: dict[str, object] | None = None) -> MethodRegister:
    """Load and validate the register. Repo path first, packaged mirror next."""
    settings = load_analysis_settings(config if config is not None else {})
    relative = Path(settings.method_register_path)
    repo_path = relative if relative.is_absolute() else Path.cwd() / relative
    if repo_path.exists():
        source = str(repo_path)
        text = repo_path.read_text(encoding="utf-8")
    else:
        packaged = _packaged_register_path(relative.name)
        if packaged is None:
            raise ValueError(
                f"Analysis method register not found: {settings.method_register_path}. "
                "Set analysis.method_register_path or restore the packaged mirror."
            )
        source = str(packaged)
        text = packaged.read_text(encoding="utf-8")

    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValidationError(f"Analysis method register must be a YAML mapping: {source}")
    try:
        register = MethodRegister.model_validate(raw)
    except Exception as exc:
        raise ValidationError(f"Malformed analysis method register {source}: {exc}") from exc
    ids = [record.id for record in register.methods]
    missing = sorted(EXPECTED_IDS - set(ids))
    duplicated = sorted({one for one in ids if ids.count(one) > 1})
    if missing or duplicated:
        raise ValidationError(
            f"Analysis method register {source}: expected ids 01 to 30 exactly once. "
            f"missing: {missing or 'none'}. duplicated: {duplicated or 'none'}."
        )
    return register

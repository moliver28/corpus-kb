"""Config-keys doctor check (PR-9 residue scope): unknown config.yaml keys.

The env check (``doctor_env.env_var_check``) warns about set-but-unread
``CORPUS_KB_*`` variables; this check applies the same config-rot
notion to the user's config files: a leaf key no runtime reader declares
is WARN material, never a failure. The known-key set is the declaration
matrix (``config_backend_matrix.declared_leaf_keys``); the unknown-key
logic is the validator's ``unknown_key_warnings`` — one implementation,
two surfaces (program rule 1). The user config file and every overlay
are scanned; a missing user file means the packaged default applies and
there is nothing to warn about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from corpus_kb._setup.doctor_checks import STATUS_OK, STATUS_WARN, ResearchCheck
from corpus_kb._setup.validate_configs_runtime import unknown_key_warnings
from corpus_kb.config import config_file_path, config_overlay_paths

DOC_ADMIN = "docs/ADMIN.md"


def _file_warnings(path: Path, label: str) -> list[str]:
    """Unknown-key warnings for one YAML file, prefixed with its label."""
    loaded: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return []
    return [f"{label}: {warning}" for warning in unknown_key_warnings(loaded)]


def config_key_check() -> ResearchCheck:
    """Warn about config keys this version does not read (config-rot tripwire)."""
    warnings: list[str] = []
    user_config = config_file_path()
    if user_config is not None and user_config.is_file():
        warnings.extend(_file_warnings(user_config, str(user_config)))
    for overlay in config_overlay_paths():
        if overlay.is_file():
            warnings.extend(_file_warnings(overlay, f"overlay {overlay}"))
    if not warnings:
        return ResearchCheck(
            name="Config keys",
            status=STATUS_OK,
            detail="no unknown config keys (the declaration matrix pins every key)",
            doc=DOC_ADMIN,
        )
    preview = "; ".join(warnings[:3])
    more = f" (+{len(warnings) - 3} more)" if len(warnings) > 3 else ""
    return ResearchCheck(
        name="Config keys",
        status=STATUS_WARN,
        detail=f"{len(warnings)} key(s) set but not read: {preview}{more}",
        fix="Remove the key, or check the spelling in docs/ADMIN.md.",
        doc=DOC_ADMIN,
    )

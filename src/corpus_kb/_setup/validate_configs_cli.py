"""File-level validation runs and the CLI for the config validator.

``validate_all`` walks the repo's MCP configs and the two runtime
``config.yaml`` copies, then reports the packaged-config lockstep result.
``main`` is the ``python src/corpus_kb/_setup/validate_configs.py``
entry point (exit 0 = all valid, 1 = errors).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# The packaged-config lockstep gate (issue #246) is stdlib+PyYAML only,
# so the same two-context pattern applies; the module reports a clean
# skip itself when corpus_kb cannot import (the slim CI job).
try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.config_lockstep import check_packaged_lockstep
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib.util

    _lockstep_path = Path(__file__).resolve().parent / "config_lockstep.py"
    _lockstep_spec = importlib.util.spec_from_file_location("config_lockstep", _lockstep_path)
    if _lockstep_spec is None or _lockstep_spec.loader is None:
        raise ImportError(f"Cannot load config_lockstep from {_lockstep_path}") from None
    _lockstep_module = importlib.util.module_from_spec(_lockstep_spec)
    _lockstep_spec.loader.exec_module(_lockstep_module)
    check_packaged_lockstep = _lockstep_module.check_packaged_lockstep

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_configs_blocks import (
        load_yaml,
        validate_extraction_gliner,
        validate_extraction_ml,
        validate_ingestion_block,
        validate_statements_block,
    )
    from corpus_kb._setup.validate_configs_consistency import check_cross_config_consistency
    from corpus_kb._setup.validate_configs_mcp import (
        validate_claude_config,
        validate_cursor_config,
        validate_opencode_config,
    )
    from corpus_kb._setup.validate_configs_runtime import validate_runtime_config
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _blocks = _importlib.import_module("validate_configs_blocks")
    load_yaml = _blocks.load_yaml
    validate_extraction_gliner = _blocks.validate_extraction_gliner
    validate_extraction_ml = _blocks.validate_extraction_ml
    validate_ingestion_block = _blocks.validate_ingestion_block
    validate_statements_block = _blocks.validate_statements_block
    _consistency = _importlib.import_module("validate_configs_consistency")
    check_cross_config_consistency = _consistency.check_cross_config_consistency
    _mcp = _importlib.import_module("validate_configs_mcp")
    validate_claude_config = _mcp.validate_claude_config
    validate_cursor_config = _mcp.validate_cursor_config
    validate_opencode_config = _mcp.validate_opencode_config
    _runtime = _importlib.import_module("validate_configs_runtime")
    validate_runtime_config = _runtime.validate_runtime_config


def load_json(path: Path) -> dict:
    """Load and parse a JSON file."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _validate_mcp_config_dir(
    project_root: Path,
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Validate mcp-configs/*.json; return (errors, claude, cursor, codex configs).

    The three mcpServers configs are returned for the cross-config check.
    """
    errors: list[str] = []
    claude_config: dict | None = None
    cursor_config: dict | None = None
    codex_config: dict | None = None

    mcp_configs_dir = project_root / "mcp-configs"
    if not mcp_configs_dir.exists():
        return ["mcp-configs/ directory not found"], None, None, None

    for json_file in sorted(mcp_configs_dir.glob("*.json")):
        config = load_json(json_file)
        filename = json_file.name

        if filename == "opencode.json":
            # Should match root format
            errs = validate_opencode_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "claude-code.json":
            claude_config = config
            errs = validate_claude_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "cursor.json":
            cursor_config = config
            errs = validate_cursor_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        elif filename == "codex.json":
            # Intentional reuse: codex.json shares the mcpServers+autoApprove shape.
            codex_config = config
            errs = validate_claude_config(config)
            errors.extend(f"mcp-configs/{filename}: {e}" for e in errs)
        else:
            errors.append(f"mcp-configs/{filename}: unknown config format, skipping")
    return errors, claude_config, cursor_config, codex_config


def _validate_runtime_yaml(project_root: Path) -> list[str]:
    """Runtime config.yaml checks: root and packaged copies must agree.

    Lockstep rule for new config keys; all three validators run per copy.
    """
    errors: list[str] = []
    for relative_path in (Path("config.yaml"), Path("src") / "corpus_kb" / "config.yaml"):
        config_path = project_root / relative_path
        display = relative_path.as_posix()
        if not config_path.exists():
            errors.append(f"{display}: not found")
            continue
        runtime_cfg = load_yaml(config_path)
        errs = validate_extraction_gliner(runtime_cfg)
        errs += validate_extraction_ml(runtime_cfg)
        errs += validate_statements_block(runtime_cfg)
        errs += validate_ingestion_block(runtime_cfg)
        errs += validate_runtime_config(runtime_cfg)
        errors.extend(f"{display}: {e}" for e in errs)
    return errors


def validate_all(project_root: Path | None = None) -> list[str]:
    """Run all validations against the project's config files.

    Returns a combined list of error strings.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    errors: list[str] = []

    # 1. Validate root opencode.json (OpenCode native format)
    opencode_path = project_root / "opencode.json"
    if opencode_path.exists():
        config = load_json(opencode_path)
        errs = validate_opencode_config(config)
        errors.extend(f"opencode.json: {e}" for e in errs)
    else:
        errors.append("opencode.json not found")

    # 2. Validate mcp-configs/*.json
    mcp_errors, claude_config, cursor_config, codex_config = _validate_mcp_config_dir(project_root)
    errors.extend(mcp_errors)

    # 3. Cross-config consistency
    opencode_config = load_json(opencode_path) if opencode_path.exists() else None

    cross_errors = check_cross_config_consistency(
        opencode_config, claude_config, cursor_config, codex_config
    )
    errors.extend(f"Cross-config: {e}" for e in cross_errors)

    # 4. Check for old format backup (informational)
    bak_path = project_root / "opencode.json.bak"
    if bak_path.exists():
        bak_config = load_json(bak_path)
        bak_errors = validate_opencode_config(bak_config)
        if not bak_errors:
            errors.append(
                "opencode.json.bak: unexpectedly passes OpenCode validation (should be old format)"
            )

    # 5. Runtime config.yaml — root and packaged copies (see helper).
    errors.extend(_validate_runtime_yaml(project_root))

    # 6. Packaged-config lockstep (issue #246): the code-side defaults and
    # the PACKAGED yaml must agree leaf-for-leaf. Skips cleanly where the
    # package cannot import (the PyYAML-only CI job); the pytest suite
    # (tests/test_config_lockstep.py) is the hard enforcement there.
    lockstep = check_packaged_lockstep()
    if not lockstep.skipped:
        errors.extend(f"config-lockstep: {e}" for e in lockstep.errors)

    return errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run validation and exit with appropriate code."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    errors = validate_all(project_root)

    if errors:
        print(f"CONFIG VALIDATION FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All config validations passed.")
        sys.exit(0)

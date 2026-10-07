#!/usr/bin/env python3
"""Generate the corpus harness command pack from the surface registry (todo 18).

Outputs (all COMMITTED — ``--check`` regenerates in memory and exits 1 on
drift, license-lock precedent):

  * ``.claude/commands/corpus-*.md``       — Claude Code slash commands
  * ``.opencode/command/corpus-*.md``      — OpenCode commands (same bodies,
    harness-specific front-matter)
  * ``mcp-configs/codex.json``             — copy-template for Codex CLI users
  * ``.claude/CLAUDE.md``                  — research-commands section between
    the registry's marker fences

STRICTLY STDLIB-ONLY, including the registry import: the registry module is
loaded by file path (``spec_from_file_location``) so this script runs on a
bare interpreter with NO dependencies installed (the agent-config-consistency
CI job). ``scripts/check_agent_configs.py`` calls ``run_harness_check`` so
registry <-> wrapper <-> config parity fails loudly (hand-editing a wrapper
without a registry change FAILS the bar).

Usage:
    python scripts/gen_harness_pack.py [--check] [--project-root PATH]

Exit code 0 = in sync (or written), 1 = drift detected.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol

REGISTRY_REL = Path("src") / "corpus_kb" / "surface_registry.py"
CLAUDE_CMDS_REL = Path(".claude") / "commands"
OPENCODE_CMDS_REL = Path(".opencode") / "command"
CODEX_JSON_REL = Path("mcp-configs") / "codex.json"
CLAUDEMD_REL = Path(".claude") / "CLAUDE.md"


def load_registry(project_root: Path) -> ModuleType:
    """Load surface_registry.py by path — never imports the corpus_kb package."""
    path = project_root / REGISTRY_REL
    if not path.exists():
        raise SystemExit(f"surface registry not found: {path}")
    spec = importlib.util.spec_from_file_location("corpus_surface_registry", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load surface registry: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _WrapperLike(Protocol):
    """Structural view of a registry Wrapper (the module loads dynamically)."""

    name: str
    description: str
    argument_hint: str
    body: str
    covers: tuple[str, ...]
    planned: bool


def _claude_wrapper_text(wrapper: _WrapperLike) -> str:
    """Claude Code slash command: YAML front-matter + registry body."""
    front = (
        f"---\ndescription: {wrapper.description}\nargument-hint: {wrapper.argument_hint}\n---\n"
    )
    return front + wrapper.body


def _opencode_wrapper_text(wrapper: _WrapperLike) -> str:
    """OpenCode command: description-only front-matter, identical body."""
    return f"---\ndescription: {wrapper.description}\n---\n" + wrapper.body


def codex_config(reg: ModuleType) -> dict[str, object]:
    """Codex CLI copy-template: same mcpServers shape as claude-code.json."""
    return {
        "mcpServers": {
            reg.SERVER_NAME: {
                "name": "Corpus-KB",
                "description": reg.SERVER_DESCRIPTION,
                "command": reg.SERVER_COMMAND,
                "args": list(reg.SERVER_ARGS),
                "env": {},
                "autoApprove": list(reg.SERVER_AUTO_APPROVE),
            }
        }
    }


def _render(project_root: Path) -> dict[Path, str]:
    """Compute every generated artifact as path -> text (LF newlines)."""
    reg = load_registry(project_root)
    out: dict[Path, str] = {}
    for wrapper in reg.ACTIVE_WRAPPERS:
        out[CLAUDE_CMDS_REL / f"{wrapper.name}.md"] = _claude_wrapper_text(wrapper)
        out[OPENCODE_CMDS_REL / f"{wrapper.name}.md"] = _opencode_wrapper_text(wrapper)
    out[CODEX_JSON_REL] = json.dumps(codex_config(reg), indent=2, ensure_ascii=False) + "\n"
    out[CLAUDEMD_REL] = _inject_claudemd_section(project_root, reg)
    return out


def _inject_claudemd_section(project_root: Path, reg: ModuleType) -> str:
    path = project_root / CLAUDEMD_REL
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = text.replace("\r\n", "\n")
    section = reg.claudemd_section()
    start = reg.CLAUDEMD_START
    end = reg.CLAUDEMD_END
    if start in text and end in text:
        head = text.split(start, 1)[0]
        tail = text.split(end, 1)[1]
        return head + section.rstrip("\n") + "\n" + tail.lstrip("\n")
    if not text.endswith("\n") and text:
        text += "\n"
    return text + "\n" + section


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def generate(project_root: Path) -> None:
    """Write every generated artifact to disk."""
    for rel_path, content in _render(project_root).items():
        path = project_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        print(f"wrote {rel_path}")


def run_harness_check(project_root: Path) -> list[str]:
    """Return drift errors (empty = registry/wrapper/config parity holds)."""
    errors: list[str] = []
    try:
        rendered = _render(project_root)
    except SystemExit as exc:
        return [str(exc)]
    for rel_path, expected in rendered.items():
        path = project_root / rel_path
        if not path.exists():
            errors.append(f"{rel_path}: generated file missing (run gen_harness_pack.py)")
        elif _normalize(path.read_text(encoding="utf-8")) != _normalize(expected):
            errors.append(
                f"{rel_path}: drifted from surface_registry.py "
                "(hand-edited wrapper — regenerate or update the registry)"
            )
    # Extra wrapper files the registry does not define.
    reg = load_registry(project_root)
    expected_names = {f"{w.name}.md" for w in reg.ACTIVE_WRAPPERS}
    for cmds_dir in (CLAUDE_CMDS_REL, OPENCODE_CMDS_REL):
        directory = project_root / cmds_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("corpus-*.md")):
            if path.name not in expected_names:
                errors.append(
                    f"{cmds_dir / path.name}: not defined in surface_registry.py "
                    "(remove it or add a Wrapper row)"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated output matches the registry; exit 1 on drift",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: parent of scripts/)",
    )
    args = parser.parse_args(argv)
    if args.check:
        errors = run_harness_check(args.project_root)
        if errors:
            print("HARNESS PACK DRIFT DETECTED:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        print("Harness pack in sync with surface_registry.py.")
        return 0
    generate(args.project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Harness command pack tests (todo 18): registry purity, generated-output
parity (wrapper bodies asserted AS DATA), codex template, CLAUDE.md section,
drift mutation gate, and the VALID_TOOL_NAMES lockstep contract.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "src" / "corpus_kb" / "surface_registry.py"
GEN_PATH = ROOT / "scripts" / "gen_harness_pack.py"

_spec = importlib.util.spec_from_file_location("surface_registry_test", REGISTRY_PATH)
assert _spec is not None and _spec.loader is not None
reg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reg)

_gen_spec = importlib.util.spec_from_file_location("gen_harness_pack_test", GEN_PATH)
assert _gen_spec is not None and _gen_spec.loader is not None
gen = importlib.util.module_from_spec(_gen_spec)
_gen_spec.loader.exec_module(gen)

_validate_spec = importlib.util.spec_from_file_location(
    "validate_configs_harness_test", ROOT / "src" / "corpus_kb" / "_setup" / "validate_configs.py"
)
assert _validate_spec is not None and _validate_spec.loader is not None
validate_configs = importlib.util.module_from_spec(_validate_spec)
_validate_spec.loader.exec_module(validate_configs)


def test_registry_is_stdlib_only() -> None:
    """r9: the agent-config-consistency job installs NO deps — any import
    beyond __future__/typing here is a ModuleNotFoundError it cannot catch."""
    tree = ast.parse(REGISTRY_PATH.read_text(encoding="utf-8"))
    allowed = {"__future__", "typing", "dataclasses"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed


def test_five_active_wrappers_plus_two_planned() -> None:
    names = [w.name for w in reg.ACTIVE_WRAPPERS]
    assert names == [
        "corpus-ingest",
        "corpus-coding-run",
        "corpus-codebook-promote",
        "corpus-research-report",
        "corpus-review",
    ]
    planned = [w.name for w in reg.WRAPPERS if w.planned]
    assert planned == ["corpus-demo", "corpus-research-cycle"]
    assert len(reg.WRAPPERS) == 7


def test_wrapper_bodies_are_invocation_guidance_only() -> None:
    for wrapper in reg.ACTIVE_WRAPPERS:
        assert "corpus-kb" in wrapper.body
        assert "--help" in wrapper.body
        assert "import " not in wrapper.body
        assert "def " not in wrapper.body
    for wrapper in reg.WRAPPERS:
        if wrapper.planned:
            assert "todo" in wrapper.body


def test_every_surface_has_cli_and_mcp_name_in_lockstep() -> None:
    for surface in reg.SURFACES:
        assert surface.cli_path
        assert surface.mcp_tool
        # r9 tool-name lockstep: every registry MCP tool is autoApprove-valid.
        assert surface.mcp_tool in validate_configs.VALID_TOOL_NAMES, surface.mcp_tool


def test_generated_claude_wrappers_match_registry() -> None:
    for wrapper in reg.ACTIVE_WRAPPERS:
        path = ROOT / ".claude" / "commands" / f"{wrapper.name}.md"
        text = path.read_text(encoding="utf-8")
        front = (
            f"---\ndescription: {wrapper.description}\n"
            f"argument-hint: {wrapper.argument_hint}\n---\n"
        )
        assert text == front + wrapper.body, path


def test_generated_opencode_wrappers_match_registry() -> None:
    for wrapper in reg.ACTIVE_WRAPPERS:
        path = ROOT / ".opencode" / "command" / f"{wrapper.name}.md"
        text = path.read_text(encoding="utf-8")
        assert text == f"---\ndescription: {wrapper.description}\n---\n" + wrapper.body, path
        claude = (ROOT / ".claude" / "commands" / f"{wrapper.name}.md").read_text(encoding="utf-8")
        # identical bodies modulo harness front-matter
        assert text.split("---\n", 2)[-1] == claude.split("---\n", 2)[-1]


def test_generated_codex_json_matches_registry_template() -> None:
    path = ROOT / "mcp-configs" / "codex.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == gen.codex_config(reg)
    server = on_disk["mcpServers"][reg.SERVER_NAME]
    claude = json.loads((ROOT / "mcp-configs" / "claude-code.json").read_text(encoding="utf-8"))
    claude_server = claude["mcpServers"][reg.SERVER_NAME]
    assert server["description"] == claude_server["description"]
    assert server["command"] == claude_server["command"]
    assert server["args"] == claude_server["args"]
    # autoApprove entries must all be valid tool names (validate_configs gate)
    errors = validate_configs.validate_claude_config(on_disk)
    assert errors == [], errors


def test_claudemd_section_matches_generated() -> None:
    text = (ROOT / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    start = text.index(reg.CLAUDEMD_START)
    end = text.index(reg.CLAUDEMD_END) + len(reg.CLAUDEMD_END)
    on_disk = text[start:end]
    generated = reg.claudemd_section().rstrip("\n")
    assert on_disk == generated


def test_harness_check_green_on_real_root() -> None:
    assert gen.run_harness_check(ROOT) == []


def test_mutation_hand_edited_wrapper_fails_check(tmp_path: Path) -> None:
    """Acceptance: edit one wrapper by hand -> check exits 1."""
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "commands").mkdir()
    (project / ".opencode" / "command").mkdir(parents=True)
    (project / "mcp-configs").mkdir(parents=True)
    (project / "src" / "corpus_kb").mkdir(parents=True)
    (project / "scripts").mkdir(parents=True)
    (project / REGISTRY_PATH.relative_to(ROOT)).write_text(
        REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / GEN_PATH.relative_to(ROOT)).write_text(
        GEN_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for rel in (
        ".claude/CLAUDE.md",
        "mcp-configs/codex.json",
        *[f".claude/commands/{w.name}.md" for w in reg.ACTIVE_WRAPPERS],
        *[f".opencode/command/{w.name}.md" for w in reg.ACTIVE_WRAPPERS],
    ):
        source = ROOT / rel
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    assert gen.run_harness_check(project) == []

    victim = project / ".claude" / "commands" / "corpus-ingest.md"
    victim.write_text(victim.read_text(encoding="utf-8") + "\nHand edit.\n", encoding="utf-8")
    errors = gen.run_harness_check(project)
    assert errors, "hand-edited wrapper must fail the drift gate"
    assert any("corpus-ingest.md" in e for e in errors)


def test_gen_harness_pack_check_subprocess_isolated_site() -> None:
    """The script must run on a bare interpreter (no site-packages): the
    agent-config-consistency CI job installs nothing."""
    proc = subprocess.run(
        [sys.executable, "-S", str(GEN_PATH), "--check", "--project-root", str(ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_registry_covers_all_todo_surfaces() -> None:
    names = {s.name for s in reg.SURFACES}
    assert {
        "research-ingest-transcript",
        "research-ingest",
        "coding-run",
        "coding-inductive",
        "codebook-promote",
        "research-report",
        "review-accept",
        "review-override",
        "notebook-ask",
        "notebook-evidence",
        "notebook-uncoded",
        "notebook-overlap",
    } <= names

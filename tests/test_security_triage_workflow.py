"""Contract tests for the security-triage workflow's weekly report PR.

Issue #513/#512 owner override (2026-11-27): each Monday run must ALSO
open or update ONE auto-PR that batches the week's findings report.
The PR carries the dated report file on a fixed chore/ branch; its body
is the human triage surface. Still no auto-fixes, and skip-honest
semantics hold: a clean run with no open report PR stays silent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "security-triage.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(workflow: dict, name_part: str) -> dict:
    steps = workflow["jobs"]["triage"]["steps"]
    for step in steps:
        if name_part in str(step.get("name", "")):
            return step
    raise AssertionError(f"step not found in security-triage.yml: {name_part}")


def _batch_step() -> dict:
    return _step(_load_workflow(), "Batch the week's report")


def test_permissions_allow_branch_and_pr_writes() -> None:
    perms = _load_workflow()["permissions"]
    assert perms["contents"] == "write"
    assert perms["pull-requests"] == "write"
    assert perms["issues"] == "write"
    assert perms["security-events"] == "read"


def test_report_pr_uses_one_fixed_chore_branch() -> None:
    script = str(_batch_step().get("run", ""))
    assert 'BRANCH="chore/security-triage-report"' in script
    assert "--base develop" in script


def test_report_pr_uses_the_changelog_release_pat_token() -> None:
    env = _batch_step().get("env") or {}
    assert env.get("GH_TOKEN") == "${{ secrets.RELEASE_PAT }}"
    assert env.get("GH_REPO") == "${{ github.repository }}"


def test_report_pr_batches_the_report_file_without_autofixes() -> None:
    script = str(_batch_step().get("run", ""))
    assert 'DOC_DIR="docs/security/triage"' in script
    assert 'REPORT_PATH="${DOC_DIR}/${REPORT_DATE}.md"' in script
    assert "gh pr create" in script
    assert "gh pr edit" in script
    # Report batching only: no sweep-style adds, no tool runs that fix.
    assert "git add -A" not in script
    assert "git add ." not in script
    assert "pip install" not in script
    assert "uv lock" not in script


def test_create_path_stages_the_report_before_the_guard() -> None:
    lines = [line.strip() for line in str(_batch_step().get("run", "")).splitlines()]
    checkout = lines.index('git checkout -b "${BRANCH}"')
    guard = next(
        i for i, line in enumerate(lines) if line.startswith("if git diff --cached --quiet")
    )
    assert checkout < guard
    between = lines[checkout + 1 : guard]
    assert 'git add "${REPORT_PATH}"' in between


def test_clean_run_with_no_open_report_pr_stays_silent() -> None:
    script = str(_batch_step().get("run", ""))
    assert "has_report=no" in script
    assert "Clean sweep and no open report PR" in script


def test_issue_thread_step_keeps_one_report_per_run() -> None:
    script = str(_step(_load_workflow(), "Append to the open triage issue").get("run", ""))
    assert "gh issue comment" in script
    assert "gh issue create" in script
    assert "has_report=yes" in script

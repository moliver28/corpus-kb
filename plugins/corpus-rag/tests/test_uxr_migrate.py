"""Command-level tests for the deprecated ``uxr migrate`` alias (issue #510).

Lives outside ``test_uxr_commands.py`` so that file stays at its
size-ratchet ceiling (412 lines). The alias prints one pointer line and
runs the ``import`` handler. No network; no live study data.

Stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, PLUGIN)

import cli_spec  # noqa: E402
import uxr_commands  # noqa: E402
import uxr_store  # noqa: E402

SPEC = {
    "ticket": "UX1-275",
    "research_questions": ["What breaks in the export flow?"],
    "hypotheses": ["H1: The export failure explains churn."],
    "analysis_questions": ["A1: Does the nav redesign hold?"],
}


def run(argv: list[str]) -> tuple[int, str]:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli_spec.HANDLERS[argv[0]](cli_spec.parse_argv(argv))
    return code, buffer.getvalue()


def setup_study(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    root = tmp_path / "study"
    (root / "interviews").mkdir(parents=True, exist_ok=True)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "corpus.json").write_text(
        json.dumps({"slug": "ux1", "uxr": {"ticket": "UX1-275"}}), encoding="utf-8"
    )
    (root / "00-overview.md").write_text("Study UX1-275 plan.\n", encoding="utf-8")
    (root / "interviews" / "p1.md").write_text(
        "**Interviewer**: hi\n**Dana (PM)**: hello\n**Participant A**: ok\n", encoding="utf-8"
    )
    spec = root / "UX1-275.spec.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    monkeypatch.chdir(root)
    return root, spec


def test_migrate_alias_prints_pointer_and_imports(tmp_path: Path, monkeypatch) -> None:
    setup_study(tmp_path, monkeypatch)
    run(["uxr", "ingest-plan", "--spec", "UX1-275.spec.json"])
    legacy = [
        {
            "session": "p1",
            "unit_id": "H1",
            "stance": "Supports",
            "quote": "export button worked",
            "quote_class": "exact",
            "rationale": "0.6.0 said so",
        }
    ]
    monkeypatch.setattr(uxr_commands, "fetch_legacy_rows", lambda slug: legacy)
    code, out = run(["uxr", "migrate"])
    assert code == 0
    assert "migrate" in out and "import" in out
    conn = uxr_store.connect("ux1")
    assert len(uxr_store.rows(conn)) == 1
    assert uxr_store.imported(conn) is True


def test_migrate_help_names_import() -> None:
    cli = Path(PLUGIN) / "bin" / "corpus-rag"
    result = subprocess.run(
        [sys.executable, str(cli), "uxr", "migrate", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "import" in result.stdout

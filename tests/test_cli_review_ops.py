"""Tests for the extracted review accept/override commands.

The commands moved out of ``cli/__init__.py`` (size-ratchet F11); these
tests pin the moved bodies against fakes — the ONE shared handler
(``review_surface.execute_review``) is monkeypatched, so no database is
touched and the full command path (argument parse, tenant, JSON receipt)
runs offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.contracts.model import DEFAULT_TENANT_ID

runner = CliRunner()


class _FakeWired:
    """The wired_store seam: a store handle and the opened DSN."""

    def __init__(self) -> None:
        self.store = SimpleNamespace(name="fake-store")
        self.conn_str = "postgresql://fake/corpus_kb"

    async def __aenter__(self) -> _FakeWired:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.parametrize(
    ("command", "decision"),
    [("accept", "accept"), ("override", "override")],
)
def test_review_decision_calls_the_shared_handler(
    command: str, decision: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command parses ids, reads the tenant, and prints the receipt."""
    import corpus_kb.cli.cli_review_ops as ops
    import corpus_kb.research.review_surface as surface

    seen: dict[str, Any] = {}

    async def fake_execute_review(
        store: object,
        tenant: UUID,
        conn_str: str,
        assignment_id: UUID,
        reviewer: str,
        decision_in: str,
        note: str,
    ) -> dict[str, object]:
        seen.update(
            store=store,
            tenant=str(tenant),
            conn_str=conn_str,
            assignment_id=str(assignment_id),
            reviewer=reviewer,
            decision=decision_in,
            note=note,
        )
        return {"status": decision_in, "assignment_id": str(assignment_id)}

    monkeypatch.setattr(ops, "wired_store", lambda **_: _FakeWired())
    monkeypatch.setattr(surface, "execute_review", fake_execute_review)

    assignment = "00000000-0000-0000-0000-00000000c0de"
    result = runner.invoke(
        app,
        [
            "review",
            command,
            assignment,
            "--reviewer",
            "reviewer-a",
            "--note",
            "link fixes describe the change",
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["status"] == decision
    assert seen["assignment_id"] == assignment
    assert seen["reviewer"] == "reviewer-a"
    assert seen["decision"] == decision
    assert seen["conn_str"] == "postgresql://fake/corpus_kb"
    # The tenant is the canonical placeholder (RLS default).
    assert UUID(seen["tenant"]) == UUID(DEFAULT_TENANT_ID)


def test_review_help_lists_accept_and_override() -> None:
    """Both decision commands register on the review sub-app."""
    result = runner.invoke(app, ["review", "--help"])
    assert result.exit_code == 0
    assert "accept" in result.output
    assert "override" in result.output

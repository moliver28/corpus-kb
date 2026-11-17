"""The ``corpus-kb update`` per-flavor plan builder (W5-a).

The plan names the upgrade steps for the detected flavor and the
restart guidance. MVP execution honesty (decision recorded in the W5-a
task): the command applies the database half only; the upgrade steps are
suggestions the user runs. The compose path reports what WOULD be pulled
and never touches the owner-controlled pin (issue #460). The pip-family
paths degrade with a typed error while the package is not on PyPI (#463).
"""

from __future__ import annotations

import pytest

from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb._setup.update_plan import UpdateStep, build_plan
from corpus_kb.errors import CorpusError, PackageNotPublishedError


def test_editable_plan_suggests_git_pull_and_reinstall() -> None:
    plan = build_plan(InstallFlavor.EDITABLE)
    commands = [step.command for step in plan.upgrade_steps]
    assert "git pull --ff-only" in commands
    assert "pip install -e ." in commands


def test_pipx_plan_suggests_pipx_upgrade() -> None:
    plan = build_plan(InstallFlavor.PIPX)
    commands = [step.command for step in plan.upgrade_steps]
    assert "pipx upgrade corpus-kb" in commands


def test_pip_plan_suggests_pip_upgrade() -> None:
    plan = build_plan(InstallFlavor.PIP)
    commands = [step.command for step in plan.upgrade_steps]
    assert "pip install --upgrade corpus-kb" in commands


def test_compose_plan_suggests_pull_and_recreate() -> None:
    plan = build_plan(InstallFlavor.COMPOSE)
    commands = [step.command for step in plan.upgrade_steps]
    assert "docker compose pull" in commands
    assert "docker compose up -d" in commands


def test_compose_plan_reports_the_pinned_images() -> None:
    """Issue #460: the compose path REPORTS what would be pulled."""
    plan = build_plan(
        InstallFlavor.COMPOSE,
        compose_images=["ghcr.io/moliver28/corpus-kb-app:0.1.0"],
    )
    reported = " ".join(plan.notes)
    assert "ghcr.io/moliver28/corpus-kb-app:0.1.0" in reported
    assert any("pin" in note for note in plan.notes)


def test_every_plan_step_names_a_label_and_a_command() -> None:
    for flavor in InstallFlavor:
        plan = build_plan(flavor)
        assert plan.upgrade_steps, flavor
        for step in plan.upgrade_steps:
            assert isinstance(step, UpdateStep)
            assert step.label
            assert step.command


def test_every_plan_carries_short_restart_guidance() -> None:
    for flavor in InstallFlavor:
        plan = build_plan(flavor)
        for sentence in plan.restart.split(". "):
            assert len(sentence.split()) <= 20, (flavor, sentence)


def test_unpublished_package_raises_the_typed_error() -> None:
    """Issue #463: the pip path names the state instead of crashing."""
    with pytest.raises(PackageNotPublishedError) as excinfo:
        build_plan(InstallFlavor.PIP, published=False)
    assert isinstance(excinfo.value, CorpusError)
    assert "PyPI" in str(excinfo.value)


def test_unpublished_pipx_raises_the_same_typed_error() -> None:
    with pytest.raises(PackageNotPublishedError):
        build_plan(InstallFlavor.PIPX, published=False)


def test_editable_and_compose_skip_the_publication_gate() -> None:
    """Their upgrade sources are not PyPI, so the gate never fires."""
    assert build_plan(InstallFlavor.EDITABLE, published=False).upgrade_steps
    assert build_plan(InstallFlavor.COMPOSE, published=False).upgrade_steps


def test_failed_publication_probe_keeps_the_plan_with_a_note() -> None:
    """A probe that cannot answer (None) keeps the plan, adds one note."""
    plan = build_plan(InstallFlavor.PIP, published=None)
    assert plan.upgrade_steps
    assert any("PyPI check failed" in note for note in plan.notes)


def test_to_dict_carries_flavor_steps_notes_restart() -> None:
    plan = build_plan(InstallFlavor.EDITABLE)
    payload = plan.to_dict()
    assert payload["flavor"] == "editable"
    assert [step["command"] for step in payload["upgrade_steps"]] == [
        step.command for step in plan.upgrade_steps
    ]
    assert payload["restart"] == plan.restart

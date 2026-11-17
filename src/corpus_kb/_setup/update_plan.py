"""The per-flavor update plan (W5-a).

The plan names the upgrade steps for the detected flavor and the restart
guidance. MVP execution honesty (the recorded W5-a decision): the
command applies the DATABASE half only — migrations and the store check.
The upgrade steps are suggestions the user runs; nothing here spawns a
package-manager subprocess or touches the running install.

Grounded semantics (W5-a grounding pass):

- ``pipx upgrade corpus-kb`` upgrades from PyPI inside the existing venv
  (pipx CLI reference) — so the pipx plan gates on the PyPI probe.
- ``docker compose pull`` re-fetches whatever tag compose.yaml pins; a
  fixed version tag never moves, so the compose plan REPORTS the pinned
  images and leaves the pin bump to the owner (issue #460).
- The plain pip plan gates on the same probe: while the package is not
  on PyPI (issue #463), the honest answer is the typed
  ``PackageNotPublishedError``, not a crash.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb.errors import PackageNotPublishedError

_PYPI_JSON_URL = "https://pypi.org/pypi/corpus-kb/json"


@dataclass(frozen=True)
class UpdateStep:
    """One suggested upgrade command, with a plain label."""

    label: str
    command: str


@dataclass(frozen=True)
class UpdatePlan:
    """What one flavor's update looks like before anything runs."""

    flavor: InstallFlavor
    upgrade_steps: tuple[UpdateStep, ...]
    notes: tuple[str, ...]
    restart: str

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload for the plan block."""
        return {
            "flavor": self.flavor.value,
            "upgrade_steps": [
                {"label": step.label, "command": step.command} for step in self.upgrade_steps
            ],
            "notes": list(self.notes),
            "restart": self.restart,
        }


def probe_pypi_published(timeout: float = 5.0) -> bool | None:
    """Ask the PyPI JSON endpoint whether ``corpus-kb`` is published.

    True = published, False = the endpoint answers 404, None = the check
    itself failed (network down, timeout) and cannot answer.
    """
    import httpx

    try:
        response = httpx.get(_PYPI_JSON_URL, timeout=timeout, follow_redirects=True)
    except (httpx.HTTPError, OSError):
        return None
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    return None


_PROBE_FAILED_NOTE = "The PyPI check failed. Run the upgrade command anyway, or try again."


def build_plan(
    flavor: InstallFlavor,
    *,
    published: bool | None = True,
    compose_images: Sequence[str] = (),
) -> UpdatePlan:
    """The plan for one flavor.

    ``published`` carries the PyPI probe answer for the pip-family
    flavors: False raises the typed not-published error (issue #463),
    None keeps the plan and adds one honest note. The editable and
    compose flavors upgrade from sources other than PyPI, so the gate
    never fires for them.
    """
    if flavor in (InstallFlavor.PIP, InstallFlavor.PIPX) and published is False:
        raise PackageNotPublishedError("corpus-kb is not on PyPI yet. The upgrade step cannot run.")
    steps, notes, restart = _PLANS[flavor]
    extra_notes: list[str] = []
    if flavor in (InstallFlavor.PIP, InstallFlavor.PIPX) and published is None:
        extra_notes.append(_PROBE_FAILED_NOTE)
    if flavor is InstallFlavor.COMPOSE:
        if compose_images:
            extra_notes.append(f"Would pull: {', '.join(compose_images)}.")
        else:
            extra_notes.append("No compose.yaml images were found next to this directory.")
        extra_notes.append(
            "The image pin in compose.yaml stays owner-controlled. Edit the pin to move versions."
        )
    return UpdatePlan(
        flavor=flavor,
        upgrade_steps=steps,
        notes=notes + tuple(extra_notes),
        restart=restart,
    )


_PLANS: dict[InstallFlavor, tuple[tuple[UpdateStep, ...], tuple[str, ...], str]] = {
    InstallFlavor.EDITABLE: (
        (
            UpdateStep("Pull the source", "git pull --ff-only"),
            UpdateStep("Install the package", "pip install -e ."),
        ),
        (),
        "Stop the server. Run corpus-kb start again.",
    ),
    InstallFlavor.PIPX: (
        (UpdateStep("Upgrade the package", "pipx upgrade corpus-kb"),),
        (),
        "Stop the server. Run corpus-kb start again.",
    ),
    InstallFlavor.PIP: (
        (UpdateStep("Upgrade the package", "pip install --upgrade corpus-kb"),),
        (),
        "Stop the server. Run corpus-kb start again.",
    ),
    InstallFlavor.COMPOSE: (
        (
            UpdateStep("Pull the new images", "docker compose pull"),
            UpdateStep("Recreate the containers", "docker compose up -d"),
        ),
        (),
        "Run docker compose up -d to recreate the containers.",
    ),
}


__all__ = ["UpdatePlan", "UpdateStep", "build_plan", "probe_pypi_published"]

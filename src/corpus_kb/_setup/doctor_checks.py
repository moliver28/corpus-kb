"""Shared check record and status values for the doctor check modules.

Every doctor check reports a :class:`ResearchCheck` with one of the
``STATUS_*`` values. The record and the values live in this leaf, so the
check modules share them without importing each other (issue #472).
"""

from __future__ import annotations

from dataclasses import dataclass

DOC_GETTING_STARTED = "docs/getting-started.md"
DOC_RESEARCH = "docs/research.md"

SETUP_FIX = "corpus-kb setup"

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_INFO = "info"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class ResearchCheck:
    """One doctor research check with its actionable remediation.

    ``embedding_dimensions`` is the structured read of the configured
    embedding width (issue #183): the detail string stays plain English,
    machines read the field. ``None`` where the check does not probe a
    width (or the config leaves it unset).
    """

    name: str
    status: str
    detail: str
    fix: str = ""
    doc: str = ""
    embedding_dimensions: int | None = None

"""The composed analysis report (WP-5): sections, composer, artifact.

``analyze_run`` (``tools/analysis_tools.py``) delegates every feature to
its ONE handler (zero-drift rule 1: compose by delegation, never
re-implement a pipeline). This module owns the report shape only:

  * :class:`AnalysisSection` — one frozen feature row: name, status,
    detail rows.
  * :func:`run_section` — calls one handler; a typed failure becomes a
    ``failed`` section. One failing section never fails the run.
  * :func:`compose_report` — the sections in fixed order plus the
    overall status.
  * :func:`write_artifact` — the deterministic snapshot: JSON at the
    output path, rendered markdown beside it, both written atomically.

Determinism contract: no wall-clock timestamps in the artifact, sorted
keys everywhere, fixed section order (dates-coverage, labeling,
classification, trends). The same corpus plus config produces
byte-identical files; each section's recorded build ``params`` ride the
detail rows instead of timestamps.

Layering: this module imports nothing from ``tools`` — the handler
callables stay in ``tools/analysis_tools.py``, which passes them in.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..errors import CorpusError

__all__ = [
    "AnalysisSection",
    "compose_report",
    "disabled_section",
    "render_markdown",
    "run_section",
    "write_artifact",
]


@dataclass(frozen=True)
class AnalysisSection:
    """One feature row of the composed report."""

    name: str
    status: str
    detail: dict[str, object] = field(default_factory=dict)


def disabled_section(name: str, reason: str, remedy: str) -> AnalysisSection:
    """The section row for a feature whose flag (or input) is missing."""
    detail: dict[str, object] = {"reason": reason, "remedy": remedy}
    return AnalysisSection(name=name, status="disabled", detail=detail)


def failed_section(name: str, reason: str, remedy: str | None) -> AnalysisSection:
    """The section row for a handler that raised; the message stays plain."""
    detail: dict[str, object] = {"reason": reason}
    if remedy is not None:
        detail["remedy"] = remedy
    return AnalysisSection(name=name, status="failed", detail=detail)


def mirror_section(name: str, envelope: dict[str, object]) -> AnalysisSection:
    """One handler envelope -> one section: status mirrored, rows kept."""
    status = str(envelope.get("status", "failed"))
    detail = {key: value for key, value in envelope.items() if key != "status"}
    return AnalysisSection(name=name, status=status, detail=detail)


async def run_section(
    name: str, run: Callable[[], Awaitable[dict[str, object]]]
) -> AnalysisSection:
    """Call one feature handler; a typed failure becomes a failed section.

    ``CorpusError`` keeps the error's remediation text. ``ValueError``
    (the labeling loader's malformed-file contract) carries the fix in
    its message, so no remedy row is added. Any other exception is NOT
    a section outcome — it fails the run loudly.
    """
    try:
        return mirror_section(name, await run())
    except CorpusError as exc:
        return failed_section(name, str(exc), exc.remediation)
    except ValueError as exc:
        return failed_section(name, str(exc), None)


def compose_report(sections: list[AnalysisSection]) -> dict[str, object]:
    """The report dict: overall status plus the sections in fixed order.

    The overall status is ``success`` when at least one section ran to
    success, else ``disabled`` (the all-flags-off stub keeps its exact
    envelope in ``analysis_tools.analyze_run``; the flags-on/no-data
    corner gains a reason there).
    """
    status = "success" if any(section.status == "success" for section in sections) else "disabled"
    return {"status": status, "sections": [asdict(section) for section in sections]}


def render_markdown(status: str, sections: list[AnalysisSection]) -> str:
    """The human-readable rendering; detail rows render as sorted JSON."""
    lines: list[str] = ["# Corpus analysis report", "", f"Status: {status}."]
    for section in sections:
        lines.append("")
        lines.append(f"## {section.name}")
        lines.append("")
        lines.append(f"Status: {section.status}.")
        for key in sorted(section.detail):
            value = json.dumps(section.detail[key], sort_keys=True, default=str)
            lines.append("")
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def write_artifact(status: str, sections: list[AnalysisSection], output: str) -> str:
    """Write the JSON report at ``output`` and the markdown beside it.

    Convention: ``output`` is the JSON path; the rendered markdown sits
    next to it with the ``.md`` suffix. ``--output reports/a.json``
    writes ``reports/a.json`` and ``reports/a.md``. Both writes are
    atomic (tmp file, then ``os.replace``); parent dirs are created.
    Returns the JSON path.
    """
    json_path = Path(output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": status, "sections": [asdict(section) for section in sections]}
    _atomic_write(json_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _atomic_write(json_path.with_suffix(".md"), render_markdown(status, sections))
    return str(json_path)


def _atomic_write(path: Path, text: str) -> None:
    """One atomic text write: tmp file in the same dir, then replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

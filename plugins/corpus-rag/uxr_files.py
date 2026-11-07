"""File writers for the UXR layer: stubs, themes, summaries (#355).

``stubs`` never overwrite. ``write-themes`` writes only between the
``corpus:begin`` and ``corpus:end`` markers in ``analysis/themes.md``
and never opens ``analysis/codes.md``. ``apply-summary`` writes a
marked block per session and is byte-stable on a second run.

Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

BEGIN = "corpus:begin"
END = "corpus:end"

# Question id lines an analyst writes by hand, like "H1: Supports."
RESEARCHER_LINE = re.compile(r"^(RQ\d+|H\d+|A\d+)\s*[:\-—]\s*(.+)$", re.IGNORECASE)

_MARKED = re.compile(r"^(.*?)(^corpus:begin$.*?^corpus:end$)(.*)$", re.S | re.M)


def replace_marked(content: str, block_text: str) -> str:
    """Replace the text between the markers, or append a marked block."""
    match = _MARKED.match(content)
    if match is None:
        block = f"{BEGIN}\n{block_text}\n{END}\n"
        if content and not content.endswith("\n"):
            content += "\n"
        return content + block
    return f"{match.group(1)}{BEGIN}\n{block_text}\n{END}{match.group(3)}"


def write_themes(root: Path, text: str) -> Path:
    """Write the generated themes into the marked block of themes.md.

    The target is fixed: ``analysis/themes.md``. There is no path
    parameter, so ``analysis/codes.md`` is unreachable by design.
    """
    themes = Path(root) / "analysis" / "themes.md"
    themes.parent.mkdir(parents=True, exist_ok=True)
    current = ""
    try:
        current = themes.read_text(encoding="utf-8")
    except OSError:
        current = ""
    themes.write_text(replace_marked(current, text.strip("\n")), encoding="utf-8")
    return themes


def write_stubs(root: Path, sources: list[dict], today: str) -> list[Path]:
    """One ``sources/corpus/<slug>.md`` per source. Never overwrite."""
    root = Path(root)
    out: list[Path] = []
    for source in sources:
        slug = str(source.get("slug") or "")
        if not slug:
            continue
        target = root / "sources" / "corpus" / f"{slug}.md"
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_stub_text(source, today), encoding="utf-8")
        out.append(target)
    return out


def _stub_text(source: dict, today: str) -> str:
    slug = str(source.get("slug") or "")
    path = str(source.get("path") or "")
    artifact = str(source.get("artifact_date") or "")
    class_ = str(source.get("class") or "source")
    return (
        "---\n"
        f"slug: {slug}\n"
        f"canonical_url: {path}\n"
        f"artifact_date: {artifact}\n"
        f"retrieved_at: {today}\n"
        "---\n\n"
        f"# {slug}\n\n"
        f"Stub for the {class_} source. The full text lives at `{path}`.\n"
        f"Cite it as [S:{slug}].\n"
    )


# ---------------------------------------------------------------------------
# apply-summary
# ---------------------------------------------------------------------------


def summary_block_text(unit_lines: dict[str, str]) -> list[str]:
    """The marked-block lines for one session, in RQ, H, A order."""
    import uxr_rollup

    pairs = [(unit, f"- {unit}: {stance}") for unit, stance in unit_lines.items()]
    pairs.sort(key=lambda pair: uxr_rollup._unit_sort_key(pair[0]))
    return [line for _unit, line in pairs]


def apply_summaries(root: Path, blocks: dict[str, list[str]]) -> list[str]:
    """Write one marked block per session summary. Existing files only."""
    root = Path(root)
    actions: list[str] = []
    for session in sorted(blocks):
        summary = root / "interviews" / f"{session}-summary.md"
        if not summary.is_file():
            actions.append(f"missing: interviews/{session}-summary.md")
            continue
        text = summary.read_text(encoding="utf-8")
        covered = researcher_qids(text)
        lines = [line for line in blocks[session] if line[2:].split(":")[0] not in covered]
        body = "\n".join(lines)
        new_text = replace_marked(text, body)
        summary.write_text(new_text, encoding="utf-8")
        actions.append(f"replaced: interviews/{session}-summary.md")
    return actions


def researcher_qids(text: str) -> set[str]:
    """The question ids the analyst's own lines already cover."""
    out: set[str] = set()
    inside_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == BEGIN:
            inside_block = True
            continue
        if stripped == END:
            inside_block = False
            continue
        if inside_block:
            continue
        match = RESEARCHER_LINE.match(stripped)
        if match:
            out.add(match.group(1).upper())
    return out

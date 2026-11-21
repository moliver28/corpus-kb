"""Gate: the plugin SKILL op table matches the engine contract catalogue.

The plugin SKILL.md files are hand-authored prose. This test pins the
op names they invoke to the live contract catalogue, so a rename on the
engine side cannot silently strand the plugin prose. It also asserts
the 13 analysis names (the 12 method ops plus the ``analysis_route``
router) are all present after the registry-spine wiring.

Stdlib test; runs from the repo root with the engine installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from corpus_kb.contracts import OPERATIONS

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "corpus-rag"
RESEARCH_SKILL = PLUGIN / "skills" / "corpus-research" / "SKILL.md"
COMMAND_FILES = sorted((PLUGIN / "commands").glob("*.md"))

OP_PATTERN = re.compile(r"corpus-rag op ([a-z_][a-z0-9_]*)")

EXPECTED_ANALYSIS_OPS = (
    "analysis_route",
    "active_learn_next",
    "analysis_select_method",
    "centroids_classify",
    "clusters_inspect",
    "contradictions_screen",
    "duplicates_screen",
    "keyphrases_extract",
    "lexical_classify",
    "neighbors_classify",
    "nli_classify",
    "outliers_rank",
    "sentiment_screen",
)

CATALOGUE_NAMES = {op.name for op in OPERATIONS}


def _op_names_in(path: Path) -> set[str]:
    """Every op name the file invokes through ``corpus-rag op <name>``."""
    text = path.read_text(encoding="utf-8")
    return set(OP_PATTERN.findall(text))


def test_every_plugin_op_name_is_in_the_catalogue() -> None:
    files = [RESEARCH_SKILL, *COMMAND_FILES]
    files += sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    for path in files:
        for name in _op_names_in(path):
            assert name in CATALOGUE_NAMES, f"{path.relative_to(PLUGIN)} names unknown op {name}"


def test_research_skill_lists_all_analysis_ops() -> None:
    names = _op_names_in(RESEARCH_SKILL)
    missing = [name for name in EXPECTED_ANALYSIS_OPS if name not in names]
    assert not missing, f"corpus-research SKILL.md is missing op rows: {missing}"


def test_expected_analysis_ops_exist_in_the_catalogue() -> None:
    missing = [name for name in EXPECTED_ANALYSIS_OPS if name not in CATALOGUE_NAMES]
    assert not missing, f"catalogue lacks expected analysis ops: {missing}"

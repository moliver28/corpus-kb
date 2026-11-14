"""Gates for the generated method-guidance artifact (novice-UX W3-ui).

``ui_src/src/api/methods.generated.ts`` carries the analysis register's
progression view for the SPA rail. Four pins: one row per register
method sorted by id, exactly the guidance keys, links that resolve
inside the register, and rung values from the difficulty vocabulary.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb._setup.gen_ui_manifest_methods import build_method_entries
from corpus_kb.analysis.method_register_progression import DIFFICULTY_LEVELS

REPO = Path(__file__).resolve().parent.parent

GUIDANCE_KEYS = {"id", "name", "summary", "op", "difficulty", "concepts", "prerequisites", "next"}


def test_one_row_per_register_method_sorted_by_id() -> None:
    """Rows cover the full 01-30 ledger exactly once, in id order."""
    entries = build_method_entries(REPO)
    ids = [str(entry["id"]) for entry in entries]
    assert ids == sorted(ids)
    assert ids == [f"{index:02d}" for index in range(1, 31)]


def test_rows_carry_exactly_the_guidance_keys() -> None:
    """Every row holds the eight guidance keys with typed values."""
    for entry in build_method_entries(REPO):
        assert set(entry) == GUIDANCE_KEYS, str(entry["id"])
        assert entry["op"] is None or isinstance(entry["op"], str)
        assert entry["difficulty"] is None or isinstance(entry["difficulty"], str)
        for field_name in ("concepts", "prerequisites", "next"):
            assert isinstance(entry[field_name], list), str(entry["id"])


def test_links_resolve_inside_the_register() -> None:
    """Every prerequisites/next entry names a registered method id."""
    entries = build_method_entries(REPO)
    ids = {str(entry["id"]) for entry in entries}
    for entry in entries:
        for field_name in ("prerequisites", "next"):
            links = entry[field_name]
            assert isinstance(links, list)
            unknown = [str(link) for link in links if str(link) not in ids]
            assert unknown == [], f"{entry['id']}.{field_name}: {unknown}"


def test_difficulty_uses_the_rung_vocabulary() -> None:
    """A difficulty is None or one of the register's three rungs."""
    for entry in build_method_entries(REPO):
        difficulty = entry["difficulty"]
        assert difficulty is None or difficulty in DIFFICULTY_LEVELS, str(entry["id"])


def test_generated_ts_lists_every_method_id() -> None:
    """The committed TypeScript table carries one row per register method."""
    methods_ts = (REPO / "ui_src/src/api/methods.generated.ts").read_text(encoding="utf-8")
    for entry in build_method_entries(REPO):
        assert f'id: "{entry["id"]}", name:' in methods_ts, str(entry["id"])

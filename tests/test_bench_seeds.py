"""Seeded-error list loader (R14 prep, TD-17 prep): valid, wrong, truncated."""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.coding.bench_seeds import (
    ERROR_TYPES,
    SEED_COLUMNS,
    load_seed_file,
    packaged_seed_set,
)
from corpus_kb.errors import SeedFileError

_HEADER = "error_id,item_id,error_type,expected_label"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "seeds.csv"
    path.write_text(f"{_HEADER}\n{body}", encoding="utf-8")
    return path


def test_module_error_type_list_is_closed() -> None:
    assert (
        frozenset(
            {
                "wrong_human_label",
                "invalid_structure",
                "unverified_quote",
                "negated_keyword",
                "bad_translation",
            }
        )
        == ERROR_TYPES
    )
    assert SEED_COLUMNS == ("error_id", "item_id", "error_type", "expected_label")


def test_valid_seed_file_loads_sorted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "ERR-002,chunk-2,negated_keyword,keyword_block_expected\n"
        "ERR-001,chunk-1,wrong_human_label,code_not_applicable\n",
    )
    seeds = load_seed_file(path)
    assert [s.error_id for s in seeds] == ["ERR-001", "ERR-002"]
    assert seeds[0].item_id == "chunk-1"
    assert seeds[0].expected_label == "code_not_applicable"


def test_quoted_multiline_label_parses_as_one_row(tmp_path: Path) -> None:
    """RFC 4180 quoting holds: a newline inside a quoted field stays in-cell."""
    body = 'ERR-001,chunk-1,wrong_human_label,"keep\nthis label"\n'
    seeds = load_seed_file(_write(tmp_path, body))
    assert len(seeds) == 1
    assert seeds[0].expected_label == "keep\nthis label"


def test_unknown_error_type_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "ERR-001,chunk-1,invented_type,label\n")
    with pytest.raises(SeedFileError, match="unknown error_type"):
        load_seed_file(path)


def test_truncated_row_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "ERR-001,chunk-1,wrong_human_label\n")
    with pytest.raises(SeedFileError, match="misses expected_label"):
        load_seed_file(path)


def test_duplicate_error_id_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "ERR-001,chunk-1,wrong_human_label,a\nERR-001,chunk-2,invalid_structure,b\n",
    )
    with pytest.raises(SeedFileError, match="repeats error_id"):
        load_seed_file(path)


def test_wrong_header_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wrong.csv"
    path.write_text("id,item,type,label\nE1,c1,wrong_human_label,x\n", encoding="utf-8")
    with pytest.raises(SeedFileError, match="header row"):
        load_seed_file(path)


def test_missing_file_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(SeedFileError, match="not readable"):
        load_seed_file(tmp_path / "absent.csv")


def test_packaged_seed_set_loads() -> None:
    seeds = packaged_seed_set()
    assert len(seeds) >= 5
    assert {s.error_type for s in seeds} <= ERROR_TYPES
    assert all(s.item_id for s in seeds)

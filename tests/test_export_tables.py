"""Codebook CSV table export (R9, TD-16): deterministic bytes, AR rules.

The golden bytes pin the data dictionary exactly (it is static, so no
timestamp flake). The other tables assert structure, ordering, and
byte-stability across runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.coding.codebook_loader import load_codebook
from corpus_kb.coding.export_tables import (
    _DATA_DICTIONARY,
    _cell,
    _csv_bytes,
    write_codebook_tables,
)
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")

_GOLDEN_DICT = (
    b"table,column,convention\n"
    b'codes,columns,"code_id, name, brief_definition, inclusion_criteria, '
    b'exclusion_criteria, actor_scope, is_provisional, created_at"\n'
    b'keywords,columns,"keyword, code_id, kind (inclusion|exclusion), source, '
    b'status (active|retired)"\n'
    b'theory,columns,"code_id, theory (a JSON object: tradition, construct, '
    b'citation, note)"\n'
    b'versions,columns,"version_id, label, sha256, paradigm, created_at"\n'
    b'releases,columns,"release_id, codebook_id, codebook_version, state, profile, '
    b'created_at, released_at"\n'
    b"all,null,An empty field means null or not applicable.\n"
    b'all,booleans,"true and false, lowercase."\n'
    b"all,timestamps,RFC 3339 UTC.\n"
    b"all,missing,A row absent from a table means the state does not exist; "
    b"no sentinel value is used.\n"
)


def _codebook() -> dict[str, object]:
    return {
        "paradigm": "codebook",
        "categories": [
            {
                "id": "C1",
                "name": "safety",
                "brief_definition": "safety behaviors",
                "inclusion_criteria": "mentions safety",
                "exclusion_criteria": "not safety",
                "examples": [],
                "theory": {
                    "tradition": "Grounded Theory",
                    "construct": None,
                    "citation": None,
                    "note": None,
                },
                "keywords": {"include": ["safety"], "exclude": ["risk"]},
            }
        ],
    }


async def _embed(text: str, instructed: bool = False) -> str:
    del text, instructed
    return "[" + ",".join(["0.0"] * 2559 + ["1.0"]) + "]"


async def _seeded(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / f"export-{uuid4().hex}.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    gw = await DuckDbGateway.connect(str(path))
    await load_codebook(_codebook(), gw, str(TENANT), _embed)
    return gw


def test_data_dictionary_golden_bytes() -> None:
    payload = _csv_bytes(("table", "column", "convention"), [list(r) for r in _DATA_DICTIONARY])
    assert payload == _GOLDEN_DICT


async def test_write_disabled_without_export_dir(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    result = await write_codebook_tables(gw, TENANT, "")
    assert result["status"] == "disabled"
    await gw.close()


async def test_tables_write_deterministic_bytes(tmp_path: Path) -> None:
    gw = await _seeded(tmp_path)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    first = await write_codebook_tables(gw, TENANT, str(out_a))
    second = await write_codebook_tables(gw, TENANT, str(out_b))
    assert first["status"] == "written"
    assert second["status"] == "written"
    assert sorted(first["files"]) == [
        "codes.csv",
        "data_dictionary.csv",
        "keywords.csv",
        "releases.csv",
        "theory.csv",
        "versions.csv",
    ]
    for name in first["files"]:
        a = (out_a / name).read_bytes()
        b = (out_b / name).read_bytes()
        assert a == b, name  # deterministic bytes across runs
        assert not a.startswith(b"\xef\xbb\xbf")  # no BOM (plain UTF-8)
        assert b"\r" not in a  # AR-1: LF endings only
        assert first["files"][name]["sha256"] == hashlib.sha256(a).hexdigest()

    # rows counts CSV records from the row list, not newline bytes.
    assert first["files"]["codes.csv"]["rows"] == 2  # header + one code row
    assert first["files"]["keywords.csv"]["rows"] == 3  # header + two keyword rows
    assert first["files"]["theory.csv"]["rows"] == 2  # header + one theory row
    assert first["files"]["releases.csv"]["rows"] == 0  # no releases: no rows, no header
    assert first["files"]["data_dictionary.csv"]["rows"] == 1 + len(_DATA_DICTIONARY)

    codes = (out_a / "codes.csv").read_text(encoding="utf-8").splitlines()
    assert codes[0].startswith("code_id,name,")
    assert "safety" in codes[1]
    assert "false,true" in codes[1] or ",false" in codes[1]  # booleans, AR-2

    keywords = (out_a / "keywords.csv").read_text(encoding="utf-8").splitlines()
    assert "safety,C1,inclusion,codebook,active" in keywords
    assert "risk,C1,exclusion,codebook,active" in keywords

    theory = (out_a / "theory.csv").read_text(encoding="utf-8").splitlines()
    assert theory[0] == "code_id,tradition,construct,citation,note"
    assert theory[1] == "C1,Grounded Theory,,,"

    await gw.close()


async def test_empty_store_writes_no_header(tmp_path: Path) -> None:
    path = tmp_path / f"blank-{uuid4().hex}.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    gw = await DuckDbGateway.connect(str(path))
    out = tmp_path / "out"
    result = await write_codebook_tables(gw, TENANT, str(out))
    assert result["status"] == "written"
    codes = (out / "codes.csv").read_bytes()
    assert codes == b""  # an empty table writes no header (no columns known)
    assert result["files"]["codes.csv"]["rows"] == 0
    assert (out / "data_dictionary.csv").read_bytes() == _GOLDEN_DICT
    await gw.close()


def test_naive_datetime_cell_assumes_utc() -> None:
    """A naive datetime is assumed UTC and emits the offset."""
    assert _cell(datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02T03:04:05+00:00"

"""The labeling ops end to end on probe pools (the test_topics_ops pattern).

Every honest state of the family is exercised at the pool/module boundary
the parity harness uses: disabled (build only), unavailable (migration
028 absent), insufficient_data (no LF file, file not found, zero LFs,
zero chunks), the full success path (score -> aggregate -> wholesale
write -> envelope), reads that are never gated, and deterministic
rebuilds (two builds produce bitwise-equal labels and stats).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.tools import labeling_tools
from corpus_kb.tools.labeling_tools import MAX_LIST_LIMIT

TENANT = "00000000-0000-0000-0000-000000000001"

_LF_DOC = {
    "classes": ["relevant", "boring"],
    "lfs": [
        {
            "name": "sla-regex",
            "kind": "regex",
            "label": "relevant",
            "config": {"pattern": r"\bSLA\b"},
        },
        {
            "name": "uptime-lex",
            "kind": "lexicon",
            "label": "relevant",
            "config": {"terms": ["uptime"]},
        },
        {
            "name": "roadmap-lex",
            "kind": "lexicon",
            "label": "relevant",
            "config": {"terms": ["roadmap"]},
        },
        {
            "name": "revenue-regex",
            "kind": "regex",
            "label": "boring",
            "config": {"pattern": r"\brevenue\b"},
        },
        {
            "name": "quarterly-lex",
            "kind": "lexicon",
            "label": "boring",
            "config": {"terms": ["quarterly"]},
        },
        {
            "name": "headcount-lex",
            "kind": "lexicon",
            "label": "boring",
            "config": {"terms": ["headcount"]},
        },
    ],
}

_RELEVANT_TEXT = "The SLA promises uptime and roadmap."
_BORING_TEXT = "Quarterly revenue and headcount moved."
_NEUTRAL_TEXT = "Nothing to see here."


def _chunk_rows() -> list[dict[str, Any]]:
    rows = []
    for i in range(4):
        rows.append(
            {
                "chunk_id": f"10000000-0000-0000-0000-{i:012d}",
                "text": _RELEVANT_TEXT,
                "metadata": "{}",
            }
        )
    for i in range(4):
        rows.append(
            {
                "chunk_id": f"20000000-0000-0000-0000-{i:012d}",
                "text": _BORING_TEXT,
                "metadata": "{}",
            }
        )
    rows.append(
        {
            "chunk_id": "30000000-0000-0000-0000-000000000000",
            "text": _NEUTRAL_TEXT,
            "metadata": "{}",
        }
    )
    return rows


class _ProbeConn:
    """Tables present, writes recorded, reads canned per table."""

    def __init__(
        self,
        chunk_rows: list[dict[str, Any]] | None = None,
        label_rows: list[dict[str, Any]] | None = None,
        lfs_rows: list[dict[str, Any]] | None = None,
        tables_present: bool = True,
    ) -> None:
        self.chunk_rows = chunk_rows or []
        self.label_rows = label_rows or []
        self.lfs_rows = lfs_rows or []
        self.tables_present = tables_present
        self.executed: list[str] = []
        self.bulk: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.fetches: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.chunk_labels" if self.tables_present else None
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(" ".join(sql.split()))
        return "OK"

    async def executemany(self, sql: str, args: list[tuple[Any, ...]]) -> None:
        self.bulk.append((" ".join(sql.split()), list(args)))

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetches.append((" ".join(sql.split()), args))
        if "FROM chunks" in sql:
            # Honor the keyset + LIMIT the way Postgres would, so the
            # batched reader sees an honest page stream.
            after, cap = str(args[1]), int(args[2])
            page = [row for row in self.chunk_rows if str(row["chunk_id"]) > after]
            return page[:cap]
        if "FROM label_functions" in sql:
            return list(self.lfs_rows)
        if "FROM chunk_labels" in sql:
            return list(self.label_rows)
        return []

    def transaction(self) -> _ProbeConn:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ProbePool:
    def __init__(self, conn: _ProbeConn) -> None:
        self._conn = conn

    def acquire(self) -> _ProbePool:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


def _enabled(tmp_path: Any, **labeling: Any) -> dict[str, object]:
    return {"analysis": {"labeling": {"enabled": True, **labeling}}}


def _write_lfs(tmp_path: Any, doc: object = None) -> str:
    path = tmp_path / "lfs.json"
    path.write_text(json.dumps(doc if doc is not None else _LF_DOC), encoding="utf-8")
    return str(path)


def _probe(**conn_kwargs: Any) -> _ProbePool:
    return _ProbePool(_ProbeConn(**conn_kwargs))


# ---------------------------------------------------------------------------
# labeling_build: every honest state
# ---------------------------------------------------------------------------


async def test_build_disabled_is_an_honest_noop() -> None:
    pool = _probe()
    result = await labeling_tools.labeling_build(pool, config={}, tenant_id=TENANT)
    assert result["status"] == "disabled"
    assert "analysis.labeling.enabled" in str(result["reason"])
    assert result["label_count"] == 0
    assert result["labels"] == []
    assert pool._conn.fetches == []  # the gate answers before any pool use


async def test_build_without_migration_is_unavailable(tmp_path: Any) -> None:
    pool = _probe(tables_present=False, chunk_rows=_chunk_rows())
    result = await labeling_tools.labeling_build(
        pool, config=_enabled(tmp_path, lfs_file=_write_lfs(tmp_path)), tenant_id=TENANT
    )
    assert result["status"] == "unavailable"
    assert "migration 028" in str(result["reason"])
    assert result["remedy"] == "run corpus-kb setup"


async def test_build_without_lfs_file_is_insufficient(tmp_path: Any) -> None:
    result = await labeling_tools.labeling_build(
        _probe(), config=_enabled(tmp_path), tenant_id=TENANT
    )
    assert result["status"] == "insufficient_data"
    assert "lfs_file is not set" in str(result["reason"])
    assert result["remedy"]


async def test_build_with_missing_file_is_insufficient(tmp_path: Any) -> None:
    config = _enabled(tmp_path, lfs_file=str(tmp_path / "absent.json"))
    result = await labeling_tools.labeling_build(_probe(), config=config, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "not found" in str(result["reason"])


async def test_build_with_zero_lfs_is_insufficient(tmp_path: Any) -> None:
    config = _enabled(tmp_path, lfs_file=_write_lfs(tmp_path, {"classes": ["a"], "lfs": []}))
    result = await labeling_tools.labeling_build(_probe(), config=config, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "zero labeling functions" in str(result["reason"])


async def test_build_with_empty_corpus_is_insufficient(tmp_path: Any) -> None:
    config = _enabled(tmp_path, lfs_file=_write_lfs(tmp_path))
    pool = _probe(chunk_rows=[])
    result = await labeling_tools.labeling_build(pool, config=config, tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "no chunks" in str(result["reason"])


async def test_build_malformed_lf_file_raises_with_the_fix(tmp_path: Any) -> None:
    config = _enabled(
        tmp_path,
        lfs_file=_write_lfs(
            tmp_path,
            {
                "classes": ["a"],
                "lfs": [{"name": "x", "kind": "spells", "label": "a", "config": {}}],
            },
        ),
    )
    with pytest.raises(ValidationError, match="Supported kinds"):
        await labeling_tools.labeling_build(_probe(), config=config, tenant_id=TENANT)


# ---------------------------------------------------------------------------
# labeling_build: the success path
# ---------------------------------------------------------------------------


async def test_build_success_envelope_and_wholesale_write(tmp_path: Any) -> None:
    conn = _ProbeConn(chunk_rows=_chunk_rows())
    pool = _ProbePool(conn)
    # min_signals above the LF count activates the honest majority-vote
    # fallback (never a fabricated EM); the labels stay exact and ties
    # would route to review.
    config = _enabled(tmp_path, lfs_file=_write_lfs(tmp_path), min_signals=7)
    result = await labeling_tools.labeling_build(pool, config=config, tenant_id=TENANT)

    assert result["status"] == "success"
    assert result["chunk_count"] == 9
    # Abstained chunks get no row: 8 labeled, the neutral one abstains.
    assert result["label_count"] == 8
    assert result["abstain_count"] == 1
    assert result["review_count"] >= result["abstain_count"]
    assert result["label_count"] + result["abstain_count"] == result["chunk_count"]

    labels = {row["chunk_id"]: row["label"] for row in result["labels"]}
    assert len(result["labels"]) == 8  # the bounded sample
    assert set(labels.values()) == {"relevant", "boring"}
    assert all(labels[cid] == "relevant" for cid in labels if cid.startswith("10000000"))
    assert all(labels[cid] == "boring" for cid in labels if cid.startswith("20000000"))
    assert result["labels"][0]["probability"] == 1.0

    # Wholesale replace: both deletes, then both bulk inserts, one txn
    # (the set_config tenant-GUC statements share the connection).
    deletes = [sql for sql in conn.executed if sql.startswith("DELETE")]
    assert deletes == [
        "DELETE FROM chunk_labels WHERE tenant_id = $1",
        "DELETE FROM label_functions WHERE tenant_id = $1",
    ]
    lf_sql, lf_args = conn.bulk[0]
    assert "INSERT INTO label_functions" in lf_sql and len(lf_args) == 6
    assert json.loads(lf_args[0][3])["label"] == "relevant"
    label_sql, label_args = conn.bulk[1]
    assert "INSERT INTO chunk_labels" in label_sql and len(label_args) == 8
    first = label_args[0]
    assert first[1].startswith("10000000") and first[2] == "relevant"
    assert 0.0 <= first[3] <= 1.0
    detail = json.loads(first[4])
    assert detail["lf_fires"] == {
        "sla-regex": "relevant",
        "uptime-lex": "relevant",
        "roadmap-lex": "relevant",
    }
    assert detail["n_signals"] == 3

    # The params record and per-LF stats ride the envelope.
    assert result["params"]["classes"] == ["relevant", "boring"]
    assert result["params"]["min_signals"] == 7
    assert result["params"]["aggregation"] == "insufficient_signals"
    assert "majority-vote fallback" in str(result["params"]["aggregation_detail"])
    assert [stat["name"] for stat in result["lf_stats"]] == [
        "sla-regex",
        "uptime-lex",
        "roadmap-lex",
        "revenue-regex",
        "quarterly-lex",
        "headcount-lex",
    ]
    assert result["lf_stats"][0]["fired"] == 4
    assert result["lf_stats"][3]["fired"] == 4
    # Three relevant and three boring LFs each co-fire fully and agree ->
    # three sorted warnings per class, six in all.
    assert len(result["warnings"]) == 6
    assert result["warnings"][0].startswith('LFs "headcount-lex" and "quarterly-lex"')
    assert result["warnings"] == sorted(result["warnings"])


async def test_build_em_path_is_deterministic_and_honest(tmp_path: Any) -> None:
    """With min_signals met the EM runs; the corpus determinism holds.

    The block-sparse LF set (per-class LFs that abstain off-class) is the
    accepted two-stage limitation (F12): the EM may converge to a weak
    fixed point and abstain widely. The contract tested here is that the
    outcome is exact across runs and the masks stay consistent.
    """
    config = _enabled(tmp_path, lfs_file=_write_lfs(tmp_path), min_signals=2)
    first = await labeling_tools.labeling_build(
        _probe(chunk_rows=_chunk_rows()), config=config, tenant_id=TENANT
    )
    second = await labeling_tools.labeling_build(
        _probe(chunk_rows=_chunk_rows()), config=config, tenant_id=TENANT
    )
    assert first["status"] == second["status"] == "success"
    assert first["params"]["aggregation"] == "ok"
    assert first["labels"] == second["labels"]
    assert first["lf_stats"] == second["lf_stats"]
    assert first["label_count"] + first["abstain_count"] == first["chunk_count"]
    assert first["review_count"] >= first["abstain_count"]


# ---------------------------------------------------------------------------
# labeling_list / labeling_lfs: reads are never gated
# ---------------------------------------------------------------------------


async def test_list_without_migration_is_unavailable(tmp_path: Any) -> None:
    pool = _probe(tables_present=False)
    result = await labeling_tools.labeling_list(pool, config=None, tenant_id=TENANT)
    assert result["status"] == "unavailable"


async def test_list_reads_are_not_gated_by_the_flag(tmp_path: Any) -> None:
    rows = [
        {
            "chunk_id": "10000000-0000-0000-0000-000000000000",
            "label": "relevant",
            "probability": 0.9,
            "created_at": None,
        }
    ]
    result = await labeling_tools.labeling_list(
        _probe(label_rows=rows), config=None, tenant_id=TENANT
    )
    assert result["status"] == "success"
    assert result["label_count"] == 1
    assert result["labels"][0]["label"] == "relevant"
    assert result["labels"][0]["chunk_id"] == rows[0]["chunk_id"]


async def test_list_limit_is_bounded(tmp_path: Any) -> None:
    conn = _ProbeConn(label_rows=[])
    await labeling_tools.labeling_list(_ProbePool(conn), config=None, tenant_id=TENANT, limit=10**9)
    reads = [entry for entry in conn.fetches if "FROM chunk_labels" in entry[0]]
    assert reads, "the label read ran"
    assert reads[0][1][1] == MAX_LIST_LIMIT


async def test_lfs_rows_carry_per_lf_stats(tmp_path: Any) -> None:
    # The stored config jsonb carries the class vote ("label") beside the
    # kind config — the registry row shape read_lfs splits back out.
    lf_rows = [
        {
            "lf_id": "lf-1",
            "name": "sla-regex",
            "kind": "regex",
            "config": {"label": "relevant", "pattern": r"\bSLA\b"},
            "created_at": None,
        },
        {
            "lf_id": "lf-2",
            "name": "web-faq",
            "kind": "metadata",
            "config": {"label": "boring", "key": "og:type", "equals": "faq"},
            "created_at": None,
        },
    ]
    chunk_rows = [
        {
            "chunk_id": "10000000-0000-0000-0000-000000000000",
            "text": "The SLA is clear.",
            "metadata": "{}",
        },
        {
            "chunk_id": "20000000-0000-0000-0000-000000000000",
            "text": "Frequently asked questions.",
            "metadata": json.dumps({"og:type": "faq"}),
        },
    ]
    result = await labeling_tools.labeling_lfs(
        _probe(chunk_rows=chunk_rows, lfs_rows=lf_rows), config=None, tenant_id=TENANT
    )
    assert result["status"] == "success"
    assert result["lf_count"] == 2
    by_name = {row["name"]: row for row in result["lfs"]}
    sla = by_name["sla-regex"]
    assert sla["stats"]["fired"] == 1
    assert sla["stats"]["coverage"] == 0.5
    assert sla["stats"]["sample_chunks"] == 2
    assert sla["label"] == "relevant"
    faq = by_name["web-faq"]
    assert faq["stats"]["fired"] == 1
    assert faq["stats"]["polarity"] == {"boring": 1.0}


async def test_lfs_without_chunks_answers_zeroed_stats(tmp_path: Any) -> None:
    lf_rows = [
        {
            "lf_id": "lf-1",
            "name": "sla-regex",
            "kind": "regex",
            "config": {"label": "relevant", "pattern": r"\bSLA\b"},
            "created_at": None,
        }
    ]
    result = await labeling_tools.labeling_lfs(
        _probe(chunk_rows=[], lfs_rows=lf_rows), config=None, tenant_id=TENANT
    )
    assert result["status"] == "success"
    assert result["lfs"][0]["stats"]["sample_chunks"] == 0
    assert result["lfs"][0]["stats"]["coverage"] == 0.0

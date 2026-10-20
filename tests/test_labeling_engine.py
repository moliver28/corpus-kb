"""The scoring engine: batched chunk reads, metadata parsing, the matrix."""

from __future__ import annotations

from typing import Any

import numpy as np

from corpus_kb.coding.aggregation import MISSING
from corpus_kb.labeling.engine import BATCH_SIZE, read_chunks, score_matrix
from corpus_kb.labeling.lfs import LFSpec, load_lfs

# ---------------------------------------------------------------------------
# score_matrix: pure, deterministic, MISSING abstains
# ---------------------------------------------------------------------------


def _spec(tmp_path: Any) -> LFSpec:
    doc = {
        "classes": ["relevant", "noise"],
        "lfs": [
            {
                "name": "sla",
                "kind": "regex",
                "label": "relevant",
                "config": {"pattern": r"\bSLA\b"},
            },
            {
                "name": "faq",
                "kind": "metadata",
                "label": "noise",
                "config": {"key": "og:type", "equals": "faq"},
            },
        ],
    }
    path = tmp_path / "lfs.json"
    import json

    path.write_text(json.dumps(doc), encoding="utf-8")
    return load_lfs(str(path))


def test_score_matrix_builds_int_matrix_with_abstains(tmp_path: Any) -> None:
    spec = _spec(tmp_path)
    from corpus_kb.labeling.engine import StoredChunk

    chunks = [
        StoredChunk("c1", "The SLA is clear.", {}),
        StoredChunk("c2", "Nothing relevant.", {"og:type": "faq"}),
        StoredChunk("c3", "Nothing at all.", {}),
    ]
    matrix = score_matrix(spec, chunks)
    assert matrix.dtype == np.dtype(int)
    assert matrix.shape == (3, 2)
    assert matrix.tolist() == [[0, MISSING], [MISSING, 1], [MISSING, MISSING]]
    again = score_matrix(spec, chunks)
    assert np.array_equal(matrix, again)  # deterministic


# ---------------------------------------------------------------------------
# read_chunks: keyset batching over the probe pool
# ---------------------------------------------------------------------------


class _BatchConn:
    """Hands back one canned page per fetch (honoring LIMIT), then stops."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[Any, ...]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(args)
        if not self.pages:
            return []
        page = self.pages.pop(0)
        return page[: args[2]] if len(args) > 2 else page

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    def transaction(self) -> _BatchConn:
        return self

    async def __aenter__(self) -> _BatchConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _BatchPool:
    def __init__(self, conn: _BatchConn) -> None:
        self._conn = conn

    def acquire(self) -> _BatchPool:
        return self

    async def __aenter__(self) -> _BatchConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _page(prefix: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": f"{prefix}-{i:012d}",
            "text": f"text {i}",
            "metadata": '{"og:type": "faq"}',
        }
        for i in range(count)
    ]


async def test_read_chunks_batches_by_chunk_id_and_keeps_order() -> None:
    conn = _BatchConn([_page("a", 3), _page("b", 2), []])
    chunks = await read_chunks(_BatchPool(conn), tenant_id="t")
    assert [chunk.chunk_id for chunk in chunks] == [
        "a-000000000000",
        "a-000000000001",
        "a-000000000002",
        "b-000000000000",
        "b-000000000001",
    ]
    # First batch starts at the UUID floor; the second starts after the
    # last id of the first page (keyset, no offset drift).
    assert conn.calls[0][1] == "00000000-0000-0000-0000-000000000000"
    assert conn.calls[1][1] == "a-000000000002"


async def test_read_chunks_metadata_jsonb_is_parsed(tmp_path: Any) -> None:
    spec = _spec(tmp_path)
    conn = _BatchConn(
        [
            [
                {"chunk_id": "a", "text": "The SLA page.", "metadata": '{"og:type": "faq"}'},
                {"chunk_id": "b", "text": "Broken json.", "metadata": "{oops"},
                {"chunk_id": "c", "text": "Null metadata.", "metadata": None},
                {"chunk_id": "d", "text": "Already parsed.", "metadata": {"og:type": "faq"}},
            ]
        ]
    )
    chunks = await read_chunks(_BatchPool(conn), tenant_id="t")
    assert chunks[0].metadata == {"og:type": "faq"}
    assert chunks[1].metadata == {}  # broken json -> empty, never a crash
    assert chunks[2].metadata == {}
    assert chunks[3].metadata == {"og:type": "faq"}
    matrix = score_matrix(spec, chunks)
    assert matrix[:, 1].tolist() == [1, MISSING, MISSING, 1]


async def test_read_chunks_respects_the_limit_across_pages() -> None:
    conn = _BatchConn([_page("a", BATCH_SIZE), _page("b", 5)])
    chunks = await read_chunks(_BatchPool(conn), tenant_id="t", limit=BATCH_SIZE + 2)
    assert len(chunks) == BATCH_SIZE + 2
    assert conn.calls[1][2] == 2  # the last batch asks only for the remainder

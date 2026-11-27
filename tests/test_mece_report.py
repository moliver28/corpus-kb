"""MECE + coverage + keyword-precision scaffold report (TD-11, T0-3).

Fixture codebook on the DuckDB gateway: deterministic one-hot vectors make
cosine outcomes exact, so every reported share and distance is asserted
exactly. No model call runs anywhere in the report (wave law).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import duckdb
import pytest
from _coding_vectors import _embed, _one_hot

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.coding._scope import CodingStore
from corpus_kb.coding.codebook_loader import load_codebook
from corpus_kb.coding.mece_report import mece_block, wilson_interval
from corpus_kb.coding.residue_terms import residue_top_terms
from corpus_kb.coding.scaffold_report import build_scaffold_report
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")


def _codebook() -> dict[str, object]:
    categories = []
    for code_id in ("C1", "C2"):
        categories.append(
            {
                "id": code_id,
                "name": f"name-{code_id}",
                "brief_definition": f"definition {code_id}",
                "inclusion_criteria": f"criteria {code_id}",
                "exclusion_criteria": f"not {code_id}",
                "examples": [],
                "theory": {},
                "keywords": {"include": [f"kw{code_id}"], "exclude": []},
            }
        )
    return {"paradigm": "codebook", "categories": categories}


async def _gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / f"mece-{uuid4().hex}.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _seed_chunk(gw: DuckDbGateway, chunk: str, text: str) -> None:
    doc_id = f"doc-{chunk}"
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, file_hash) VALUES ($1,$2,$3,$4)"
        " ON CONFLICT DO NOTHING",
        doc_id,
        str(TENANT),
        f"file://{doc_id}.txt",
        uuid4().hex,
    )
    await gw.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, metadata)"
        " VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
        chunk,
        str(TENANT),
        doc_id,
        0,
        text,
        "{}",
    )
    literal = "[" + ",".join(format(float(v), ".9g") for v in _one_hot(text, True)) + "]"
    await gw.execute(
        "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding) VALUES ($1,$2,$3::FLOAT[2560])"
        " ON CONFLICT (chunk_id) DO NOTHING",
        chunk,
        str(TENANT),
        literal,
    )


async def _fixture_world(tmp_path: Path) -> DuckDbGateway:
    gw = await _gateway(tmp_path)
    version = await load_codebook(_codebook(), gw, str(TENANT), _embed)
    # Four chunks: two coded C1, one coded C2, one residue chunk.
    for chunk, text in (
        ("11111111-1111-1111-1111-111111111111", "alpha text one"),
        ("22222222-2222-2222-2222-222222222222", "alpha text two"),
        ("33333333-3333-3333-3333-333333333333", "beta text three"),
        ("44444444-4444-4444-4444-444444444444", "residue text four"),
    ):
        await _seed_chunk(gw, chunk, text)
    for chunk, code in (
        ("11111111-1111-1111-1111-111111111111", "C1"),
        ("22222222-2222-2222-2222-222222222222", "C1"),
        ("33333333-3333-3333-3333-333333333333", "C2"),
    ):
        await gw.execute(
            "INSERT INTO chunk_codes (chunk_id, code_id, tenant_id, coder, model,"
            " decision, confidence, evidence_quote) VALUES ($1,$2,$3,'bench','m',"
            "'assign',0.9,'quote') ON CONFLICT DO NOTHING",
            chunk,
            code,
            str(TENANT),
        )
    for chunk, code in (
        ("11111111-1111-1111-1111-111111111111", "C1"),
        ("22222222-2222-2222-2222-222222222222", "C1"),
        ("33333333-3333-3333-3333-333333333333", "C2"),
        ("44444444-4444-4444-4444-444444444444", "C1"),
        ("44444444-4444-4444-4444-444444444444", "C2"),
    ):
        await gw.execute(
            "INSERT INTO chunk_keyword_hits (chunk_id, keyword, code_id, tenant_id, kind, n_hits)"
            " VALUES ($1,'k', $2, $3, 'inclusion', 1) ON CONFLICT DO NOTHING",
            chunk,
            code,
            str(TENANT),
        )
    # An exclusion hit on chunk one (distinct keyword, so the hits PK holds).
    # Exclusion rows must NOT count as keyword coding: the share stays 0.25
    # even though this row gives chunk one a second (exclusion) code hit.
    await gw.execute(
        "INSERT INTO chunk_keyword_hits (chunk_id, keyword, code_id, tenant_id, kind, n_hits)"
        " VALUES ('11111111-1111-1111-1111-111111111111','kx','C2',$1,'exclusion',1)"
        " ON CONFLICT DO NOTHING",
        str(TENANT),
    )
    # tier-0 signal rows exist for three of four chunks (C1 pooled only).
    for chunk in (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ):
        await gw.execute(
            "INSERT INTO chunk_signals (chunk_id, code_id, tenant_id, signal, score)"
            " VALUES ($1,'C1',$2,'vector',0.9) ON CONFLICT DO NOTHING",
            chunk,
            str(TENANT),
        )
    del version
    return gw


async def test_build_scaffold_report_numbers(tmp_path: Path) -> None:
    gw = await _fixture_world(tmp_path)
    codes = await gw.fetch(
        "SELECT codebook_version_id FROM code_registry WHERE tenant_id=$1 LIMIT 1", str(TENANT)
    )
    version_id = UUID(str(codes[0]["codebook_version_id"]))
    report = await build_scaffold_report(gw, TENANT, version_id)

    mece = report["mece"]
    assert mece["status"] == "success"
    assert mece["embedder"] == "qwen3-embedding:4b"  # DuckDB default model name
    assert mece["n_chunks"] == 4
    ex = mece["exclusivity"]
    # one chunk (residue) carries hits from two codes; no chunk has two assignments.
    assert ex["multi_automatic_code_share"]["value"] == 0.0
    assert ex["multi_automatic_code_share"]["n"] == 4
    assert ex["multi_keyword_code_share"]["value"] == 0.25
    assert ex["mean_centroid_cosine_distance"]["n_codes"] == 2
    # orthogonal one-hot definitions -> cosine distance 1.0 exactly.
    assert ex["mean_centroid_cosine_distance"]["value"] == pytest.approx(1.0)

    exh = mece["exhaustiveness"]
    assert exh["coded_share"]["value"] == pytest.approx(0.75)
    assert exh["residue_size"] == 1
    assert exh["residual"]["n"] == 1

    gaps = mece["coverage_gaps"]
    by_code = {row["code_id"]: row for row in gaps["per_code"]}
    assert by_code["C1"]["n_missing"] == 1  # residue chunk has no tier-0 row
    assert by_code["C2"]["n_missing"] == 4  # no C2 tier-0 rows at all

    precision = report["keyword_precision"]
    assert precision["n_keywords"] == 2
    assert all(kw["precision"] == "not_measured" for kw in precision["keywords"])

    scaffold = report["scaffold"]
    assert scaffold["lines"], "the owner-facing report carries lines"
    assert any("Embedder:" in line for line in scaffold["lines"])
    assert any("tier-0 row" in line for line in scaffold["lines"])
    seed_bar = scaffold["seed_bar"]
    assert seed_bar["grade_a_bar"] == 59
    assert seed_bar["packaged_seed_rows"] >= 5
    assert scaffold["ledger_sample"] == []  # no coding_pool op ran in this fixture
    await gw.close()


def _gateway_stub(exc: Exception) -> CodingStore:
    """A gateway-shaped store whose reads raise ``exc`` (the tenant seam)."""

    class BoomGateway:
        async def __aenter__(self) -> BoomGateway:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def transaction(self, _tenant_id: str) -> BoomGateway:
            return self

        async def fetch(self, *_args: object, **_kw: object) -> list[object]:
            raise exc

        async def fetchval(self, *_args: object, **_kw: object) -> object:
            raise exc

    return cast(CodingStore, BoomGateway())


async def test_scaffold_report_degrades_on_missing_tables() -> None:
    """A missing coding table degrades; any other bug must surface."""
    missing: list[Exception] = [
        asyncpg.UndefinedTableError("relation does not exist"),
        duckdb.CatalogException("Table with name chunks does not exist"),
    ]
    for exc in missing:
        report = await build_scaffold_report(_gateway_stub(exc), TENANT, uuid4())
        assert report["mece"]["status"] == "unavailable"
        assert report["keyword_precision"]["status"] == "unavailable"


async def test_scaffold_report_bugs_surface_instead_of_degrading() -> None:
    with pytest.raises(RuntimeError, match="a shipped bug"):
        await build_scaffold_report(_gateway_stub(RuntimeError("a shipped bug")), TENANT, uuid4())


def test_wilson_interval_bounds() -> None:
    assert wilson_interval(0, 0) == [0.0, 0.0]
    low, high = wilson_interval(3, 10)
    assert 0.0 <= low <= 3 / 10 <= high <= 1.0
    all_hits = wilson_interval(10, 10)
    assert all_hits[0] > 0.69 and all_hits[1] <= 1.0


def test_mece_block_pure_math_on_synthetic_rows() -> None:
    inputs = {
        "codes": [
            {"code_id": "C1", "name": "a", "definition_vector": [1.0, 0.0]},
            {"code_id": "C2", "name": "b", "definition_vector": [0.0, 1.0]},
        ],
        "vector_rows": [
            {
                "chunk_id": "c1",
                "embedding": [1.0, 0.0],
                "embedding_model": "m",
                "text": "dog dog cat",
            },
            {"chunk_id": "c2", "embedding": [1.0, 0.0], "embedding_model": "m", "text": "dog runs"},
            {"chunk_id": "c3", "embedding": [0.0, 1.0], "embedding_model": "m", "text": "cat sits"},
            {"chunk_id": "c4", "embedding": None, "embedding_model": "m", "text": "skipped"},
        ],
        "coded": [
            {"chunk_id": "c1", "code_id": "C1"},
            {"chunk_id": "c2", "code_id": "C1"},
            {"chunk_id": "c3", "code_id": "C2"},
        ],
        "keyword_hits": [
            {"chunk_id": "c1", "code_id": "C1"},
            {"chunk_id": "c1", "code_id": "C2"},
            {"chunk_id": "c3", "code_id": "C2"},
        ],
    }
    mece = mece_block(inputs)
    assert mece["n_chunks"] == 3  # the zero-vector row never enters
    assert mece["exclusivity"]["multi_keyword_code_share"]["value"] == pytest.approx(1 / 3)
    assert mece["exclusivity"]["mean_centroid_cosine_distance"]["value"] == pytest.approx(1.0)
    assert mece["exhaustiveness"]["coded_share"]["value"] == 1.0
    assert mece["exhaustiveness"]["residue_size"] == 0


def test_silhouette_mean_covers_all_scored_groups() -> None:
    """A negative-separation group must lower the mean and raise n_groups.

    c1/c2 share label C1 but sit orthogonal; c3 is a C2 singleton (score
    0). Per-group means: C1 scores -0.4 (the group mean carries the
    self-distance: a=0.5 against b=0.4 and 0.2), C2 scores 0. The honest
    mean is -0.2 over 2 groups; the old positive-only mean reported None/0.
    """
    inputs = {
        "codes": [
            {"code_id": "C1", "name": "a", "definition_vector": [1.0, 0.0]},
            {"code_id": "C2", "name": "b", "definition_vector": [0.0, 1.0]},
        ],
        "vector_rows": [
            {"chunk_id": "c1", "embedding": [1.0, 0.0], "embedding_model": "m", "text": "t1"},
            {"chunk_id": "c2", "embedding": [0.0, 1.0], "embedding_model": "m", "text": "t2"},
            {"chunk_id": "c3", "embedding": [0.6, 0.8], "embedding_model": "m", "text": "t3"},
        ],
        "coded": [
            {"chunk_id": "c1", "code_id": "C1"},
            {"chunk_id": "c2", "code_id": "C1"},
            {"chunk_id": "c3", "code_id": "C2"},
        ],
        "keyword_hits": [],
    }
    mece = mece_block(inputs)
    silhouette = mece["exclusivity"]["silhouette"]
    assert silhouette["n_groups"] == 2  # the true group count, not positives
    assert silhouette["value"] == pytest.approx(-0.2)


def test_embedder_is_unknown_when_no_vector_parses() -> None:
    """With zero parseable vectors the report must not name a default model."""
    inputs = {
        "codes": [],
        "vector_rows": [
            {
                "chunk_id": "c1",
                "embedding": None,
                "embedding_model": "qwen3-embedding:4b",
                "text": "t",
            },
            {"chunk_id": "c2", "embedding": "[not json", "embedding_model": "m", "text": "t"},
        ],
        "coded": [],
        "keyword_hits": [],
    }
    assert mece_block(inputs)["embedder"] == "unknown"


def test_residue_top_terms_strip_punctuation() -> None:
    """'coding,' must count as 'coding', not be dropped by the alpha check."""
    terms = residue_top_terms({0: ["Coding, quality; coding wins. it the", "code!"]})
    words = [word for word, _n in terms[0]["top_terms"]]
    assert words[0] == "coding"  # two mentions, one punctuated
    assert words[1:] == ["code", "quality", "wins"]  # count desc, then word
    assert "it" not in words and "the" not in words  # stopwords stay out

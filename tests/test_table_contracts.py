"""Table-contract gate: declared register tables match real op rows.

For every method whose register row declares an ``output_table`` this
module seeds a fixture corpus (documents, chunks, stored vectors,
labels, predictions) on ONE backend, runs the op through its SHARED
handler (the same function every surface routes through), and asserts
the declared contract:

  1. result-row key set == the declared column names, in order;
  2. every numeric cell sits at the declared rounding precision;
  3. the rows come back in the declared order (a declared ``-field``
     entry sorts descending, the rest ascending);
  4. every declared denominator key rides the envelope;
  5. crafted tie data produces the declared tiebreaker order.

The whole matrix runs twice: on the local Postgres (``corpus_kb_test``;
the leg SKIPS, visibly, when no server answers) and on an in-process
migrated DuckDB file. Model-dependent arms are honest about their
dependency: the embedding keyphrase leg probes the live embedder and
skips with a printed reason when no model answers; the NLI and LLM
keyphrase legs can their model transport (deterministic canned verdicts)
so the store-side row assembly stays provable without a model.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from itertools import pairwise
from typing import Any
from uuid import uuid4

import pytest

from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_tables import OutputTable
from corpus_kb.storage.gateway import DuckDbGateway, is_gateway
from tests.live_embed import live_embed_dimensions, live_embed_model

TENANT = str(uuid4())

# Crafted, sortable chunk ids (lexicographic on the uuid text, so tie
# orders are assertable without guessing at handler internals).
_AA = "aaaaaaaa-0000-0000-0000-000000000001"
_BB = "bbbbbbbb-0000-0000-0000-000000000002"
_CC = "cccccccc-0000-0000-0000-000000000003"
_DD = "dddddddd-0000-0000-0000-000000000004"

_WIDTH = 2560


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture()
async def both_pool(request: pytest.FixtureRequest, tmp_path: Any) -> AsyncIterator[Any]:
    """Parametrized backend: the local Postgres, else the DuckDB file.

    The Postgres arm opens its own pool (the conftest ``pg_pool`` DSN
    and skip rule) instead of nesting that fixture: a nested async
    fixture setup cannot run inside this one on this pytest-asyncio
    version. A server-less host SKIPS the leg — visible, never silent.
    """
    if request.param == "postgres":
        import asyncpg

        dsn = os.environ.get(
            "CORPUS_KB_DATABASE_URL",
            "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test",
        )
        try:
            pool: Any = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=5)
        except Exception:
            pytest.skip("Postgres not available")
        yield pool
        await _cleanup(pool, TENANT)
        await pool.close()
        return
    from corpus_kb._setup.migrate import run_duckdb_migrations

    dsn = f"duckdb:///{(tmp_path / 'tables.duckdb').as_posix()}"
    run_duckdb_migrations(dsn)
    gateway = await DuckDbGateway.connect(dsn)
    yield gateway
    await gateway.close()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Give the both_pool fixture readable ids (postgres / duckdb)."""
    if "both_pool" in metafunc.fixturenames:
        metafunc.parametrize("both_pool", ["postgres", "duckdb"], indirect=True)


async def _cleanup(pool: Any, tenant: str) -> None:
    """Delete the tenant's seeded rows, children first (Postgres leg only)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant) as conn:
        await conn.execute("DELETE FROM chunk_predictions WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM label_sets WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM chunk_labels WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM chunks_vectors WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM chunks WHERE tenant_id = $1", tenant)
        await conn.execute("DELETE FROM documents WHERE tenant_id = $1", tenant)


# ---------------------------------------------------------------------------
# Seeding helpers


def _vec(*components: float) -> list[float]:
    """One 2560-wide vector from up to 8 leading components."""
    head = list(components) + [0.0] * (_WIDTH - len(components))
    return head[:_WIDTH]


def _literal(values: list[float]) -> str:
    """The bracketed text form both engines parse."""
    return "[" + ",".join(format(float(v), ".9g") for v in values) + "]"


_DOC_ID = "eeeeeeee-0000-0000-0000-000000000001"


async def _seed(
    pool: Any,
    tenant: str,
    vectors: dict[str, list[float]] | None = None,
    texts: dict[str, str] | None = None,
) -> None:
    """One document, its chunks, and any stored vectors (both engines).

    Idempotent inserts, so tests can seed chunks and vectors in two
    calls (the lexical leg needs texts first, vectors second).
    """
    from corpus_kb.storage.tenant_conn import tenant_connection

    vectors = vectors or {}
    texts = texts or {}
    ids = sorted(set(vectors) | set(texts))
    if not ids:
        raise ValueError("seed nothing: pass vectors or texts")
    vector_cast = "$3::FLOAT[2560]" if is_gateway(pool) else "$3::halfvec"
    async with tenant_connection(pool, tenant) as conn:
        await conn.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ($1::uuid, $2, $3, $4) ON CONFLICT DO NOTHING",
            _DOC_ID,
            tenant,
            "table-contract seed",
            "text",
        )
        for position, chunk_id in enumerate(ids):
            await conn.execute(
                "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
                " VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6) ON CONFLICT DO NOTHING",
                chunk_id,
                tenant,
                _DOC_ID,
                position,
                texts.get(chunk_id, f"text {chunk_id[:4]}"),
                "text",
            )
            if chunk_id in vectors:
                await conn.execute(
                    "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding)"
                    f" VALUES ($1::uuid, $2, {vector_cast}) ON CONFLICT DO NOTHING",
                    chunk_id,
                    tenant,
                    _literal(vectors[chunk_id]),
                )


async def _seed_labels(pool: Any, tenant: str, rows: list[tuple[str, str, float]]) -> None:
    """Direct chunk_labels rows: (chunk_id, label, probability)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant) as conn:
        for chunk_id, label, probability in rows:
            await conn.execute(
                "INSERT INTO chunk_labels (tenant_id, chunk_id, label, probability, detail)"
                " VALUES ($1, $2::uuid, $3, $4, $5::jsonb)",
                tenant,
                chunk_id,
                label,
                probability,
                json.dumps({"seed": "table-contract"}),
            )


async def _seed_predictions(
    pool: Any, tenant: str, rows: list[tuple[str, str, float, bool]]
) -> None:
    """One label set plus chunk_predictions rows: (chunk_id, label, p, review)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    set_id = "99999999-0000-0000-0000-000000000001"
    async with tenant_connection(pool, tenant) as conn:
        await conn.execute(
            "INSERT INTO label_sets (set_id, tenant_id, name, classes)"
            " VALUES ($1::uuid, $2, $3, $4::jsonb)",
            set_id,
            tenant,
            "table-contract set",
            json.dumps(["neg", "pos"]),
        )
        for chunk_id, label, probability, review in rows:
            await conn.execute(
                "INSERT INTO chunk_predictions (tenant_id, chunk_id, set_id, label, probability,"
                " tier, review) VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7)",
                tenant,
                chunk_id,
                set_id,
                label,
                probability,
                "head",
                review,
            )


# ---------------------------------------------------------------------------
# Contract assertion helpers


def _table(method_id: str) -> OutputTable:
    """The declared table for one method id (register truth)."""
    record = next(row for row in load_method_register({}).methods if row.id == method_id)
    assert record.output_table is not None, f"{method_id} declares no table"
    return record.output_table


def _assert_rows_match_columns(method_id: str, rows: list[dict[str, Any]]) -> None:
    """Row keys, in order, equal the declared columns on every row."""
    declared = [column.name for column in _table(method_id).columns]
    for row in rows:
        assert list(row.keys()) == declared, (
            f"{method_id}: row keys {list(row.keys())} != declared {declared}"
        )


def _assert_precision(method_id: str, rows: list[dict[str, Any]]) -> None:
    """Every numeric cell sits at the declared precision."""
    table = _table(method_id)
    for row in rows:
        for column in table.columns:
            value = row[column.name]
            if column.type != "number":
                continue
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                f"{method_id}: {column.name}={value!r} is not a number cell"
            )
            assert table.precision is not None, f"{method_id}: number column, no precision"
            assert value == round(float(value), table.precision), (
                f"{method_id}: {column.name}={value} breaks {table.precision}-place rounding"
            )


def _assert_order(method_id: str, rows: list[dict[str, Any]]) -> None:
    """Rows are sorted by the declared keys; a ``-`` prefix means descending."""
    table = _table(method_id)
    if "model_rank" in table.order_by:
        return  # the producer's reply order; the op's test pins it directly
    for left, right in pairwise(rows):
        for key in table.order_by:
            descending = key.startswith("-")
            field = key.lstrip("-")
            first, second = left[field], right[field]
            if first == second:
                continue
            if descending:
                assert first > second, f"{method_id}: rows break the {key} order"
            else:
                assert first < second, f"{method_id}: rows break the {key} order"
            break


def _assert_denominators(method_id: str, envelope: dict[str, Any]) -> None:
    """Every declared denominator key rides the envelope."""
    for denominator in _table(method_id).denominators or []:
        assert denominator in envelope, f"{method_id}: denominator {denominator} absent"


def _assert_contract(method_id: str, envelope: dict[str, Any], rows_key: str) -> None:
    """The full declared contract against one success envelope."""
    assert envelope["status"] == "success", f"{method_id}: {envelope.get('status')}"
    rows = envelope[rows_key]
    _assert_rows_match_columns(method_id, rows)
    _assert_precision(method_id, rows)
    _assert_order(method_id, rows)
    _assert_denominators(method_id, envelope)


# ---------------------------------------------------------------------------
# The contract matrix (one test per declared method)


async def test_29_duplicates_screen_contract(both_pool: Any) -> None:
    """Pairs carry (similarity, id_i, id_j); equal similarities tie by id."""
    from corpus_kb.tools import vector_screen_tools

    await _seed(
        both_pool,
        TENANT,
        {_AA: _vec(1.0, 0.0), _BB: _vec(1.0, 0.0), _CC: _vec(0.0, 1.0), _DD: _vec(0.0, 1.0)},
    )
    envelope = await vector_screen_tools.duplicates_screen(both_pool, {}, TENANT, threshold=0.9)
    _assert_contract("29", envelope, "pairs")
    assert [(pair["chunk_id_i"], pair["chunk_id_j"]) for pair in envelope["pairs"]] == [
        (_AA, _BB),
        (_CC, _DD),
    ]


async def test_30_outliers_rank_contract(both_pool: Any) -> None:
    """Collinear vectors give every row distance 0; ties order by chunk id."""
    from corpus_kb.tools import vector_screen_tools

    await _seed(both_pool, TENANT, {_AA: _vec(0.5), _BB: _vec(1.0), _CC: _vec(2.0)})
    envelope = await vector_screen_tools.outliers_rank(both_pool, {}, TENANT, k=1)
    _assert_contract("30", envelope, "outliers")
    assert [row["chunk_id"] for row in envelope["outliers"]] == [_AA, _BB, _CC]


async def test_12_clusters_inspect_contract(both_pool: Any) -> None:
    """Two tight pairs: equal-size clusters tie and order by cluster id."""
    from corpus_kb.tools import cluster_tools

    await _seed(
        both_pool,
        TENANT,
        {
            _AA: _vec(5.0, 0.0),
            _BB: _vec(5.1, 0.1),
            _CC: _vec(0.0, 5.0),
            _DD: _vec(0.1, 5.1),
        },
    )
    envelope = await cluster_tools.clusters_inspect(both_pool, {}, TENANT, min_cluster_size=2)
    _assert_contract("12", envelope, "clusters")
    sizes = [row["size"] for row in envelope["clusters"]]
    assert sizes == sorted(sizes, reverse=True)
    ids = [row["cluster_id"] for row in envelope["clusters"]]
    assert ids == sorted(ids)


async def test_13_centroids_classify_contract(both_pool: Any) -> None:
    """Equidistant target: margin 0 abstains and picks the first label."""
    from corpus_kb.tools import centroid_tools

    await _seed(
        both_pool,
        TENANT,
        {_AA: _vec(1.0, 0.0), _BB: _vec(0.0, 1.0), _CC: _vec(1.0, 1.0), _DD: _vec(1.0, 0.001)},
    )
    await _seed_labels(both_pool, TENANT, [(_AA, "beta", 0.9), (_BB, "alpha", 0.9)])
    envelope = await centroid_tools.centroids_classify(both_pool, {}, TENANT)
    _assert_contract("13", envelope, "predictions")
    by_id = {row["chunk_id"]: row for row in envelope["predictions"]}
    tie = by_id[_CC]
    assert tie["abstained"] is True
    assert tie["label"] == "alpha"  # the declared tiebreaker: first label wins
    assert tie["margin"] == 0.0
    assert by_id[_DD]["abstained"] is False


async def test_14_neighbors_classify_contract(both_pool: Any) -> None:
    """One vote each: the vote tie goes to the alphabetically first label."""
    from corpus_kb.tools import neighbor_tools

    await _seed(both_pool, TENANT, {_AA: _vec(1.0, 0.0), _BB: _vec(0.0, 1.0), _CC: _vec(1.0, 1.0)})
    await _seed_labels(both_pool, TENANT, [(_AA, "beta", 0.9), (_BB, "alpha", 0.9)])
    envelope = await neighbor_tools.neighbors_classify(both_pool, {}, TENANT, k=2)
    _assert_contract("14", envelope, "predictions")
    row = envelope["predictions"][0]
    assert row["chunk_id"] == _CC
    assert row["label"] == "alpha"
    assert [vote["label"] for vote in row["votes"]] == ["alpha", "beta"]
    assert [vote["votes"] for vote in row["votes"]] == [1, 1]


async def test_15_lexical_classify_contract(both_pool: Any) -> None:
    """The head scores the whole window; rows order by chunk id."""
    from corpus_kb.tools import lexical_tools

    await _seed(
        both_pool,
        TENANT,
        texts={
            _AA: "bank loan interest rate",
            _BB: "river bank water flow",
            _CC: "loan approval credit score",
            _DD: "river shore water current",
        },
    )
    await _seed(
        both_pool,
        TENANT,
        {_AA: _vec(1.0), _BB: _vec(0.0), _CC: _vec(1.0), _DD: _vec(0.0)},
    )
    await _seed_labels(
        both_pool,
        TENANT,
        [(_AA, "finance", 0.9), (_BB, "nature", 0.9), (_CC, "finance", 0.8), (_DD, "nature", 0.8)],
    )
    envelope = await lexical_tools.lexical_classify(both_pool, {}, TENANT)
    _assert_contract("15", envelope, "predictions")
    ids = [row["chunk_id"] for row in envelope["predictions"]]
    assert ids == sorted(ids)


async def test_18_nli_classify_contract(both_pool: Any, monkeypatch: Any) -> None:
    """Canned verdicts: score ties pick the alphabetically first label."""
    import corpus_kb.analysis.nli_contract as nli_contract_module
    from corpus_kb.tools import nli_tools

    async def canned(prompt: str, model: str, base_url: str) -> str:
        return "ENTAIL"

    monkeypatch.setattr(nli_contract_module, "judge_verdict", canned)
    await _seed(both_pool, TENANT, {_AA: _vec(1.0, 0.0), _BB: _vec(0.0, 1.0)})
    envelope = await nli_tools.nli_classify(both_pool, {}, TENANT, labels=["zulu", "alpha"])
    _assert_contract("18", envelope, "predictions")
    assert envelope["screened_count"] == 2
    for row in envelope["predictions"]:
        assert row["label"] == "alpha"  # both scores 2.0: the declared tie rule
        assert [score["label"] for score in row["scores"]] == ["alpha", "zulu"]


async def test_05_keyphrases_tfidf_contract(both_pool: Any) -> None:
    """Shared-score phrases tie and order alphabetically."""
    from corpus_kb.tools import keyphrase_tools

    text = "alpha beta alpha report"
    other = "gamma delta summary"
    await _seed(
        both_pool,
        TENANT,
        {_AA: _vec(1.0), _BB: _vec(1.0), _CC: _vec(0.0, 1.0)},
        texts={_AA: text, _BB: text, _CC: other},
    )
    envelope = await keyphrase_tools.keyphrases_extract(both_pool, {}, TENANT, variant="tfidf")
    _assert_contract("05", envelope, "keyphrases")
    scores = {row["phrase"]: row["score"] for row in envelope["keyphrases"]}
    assert scores["delta"] == scores["gamma"]  # the crafted tie
    tied = [row for row in envelope["keyphrases"] if row["phrase"] in ("delta", "gamma")]
    assert [row["phrase"] for row in tied] == ["delta", "gamma"]


@pytest.mark.requires_ollama
async def test_06_keyphrases_embedding_contract(both_pool: Any) -> None:
    """Live embedder ranks phrases by cosine to the corpus centroid."""
    from corpus_kb.rag import embedder as embedder_mod
    from corpus_kb.tools import keyphrase_tools

    probe = await embedder_mod.aembed_batch(
        embedder_mod.create_embedder(
            {
                "embedding": {
                    "provider": "ollama",
                    "model": live_embed_model(),
                    "dimensions": live_embed_dimensions(),
                }
            }
        ),
        ["embedder reachability probe"],
    )
    if all(all(value == 0.0 for value in vector) for vector in probe):
        pytest.skip("no live embedding model answered (declared skip, never silent)")
    await _seed(
        both_pool,
        TENANT,
        texts={_AA: "river bank water flow", _BB: "bank loan interest rate"},
    )
    envelope = await keyphrase_tools.keyphrases_extract(both_pool, {}, TENANT, variant="embedding")
    _assert_contract("06", envelope, "keyphrases")
    assert envelope["screened_count"] == 2


async def test_07_keyphrases_llm_contract(both_pool: Any, monkeypatch: Any) -> None:
    """Canned model reply: rows keep the reply order with placeholder scores."""
    from corpus_kb.tools import keyphrase_llm, keyphrase_tools

    monkeypatch.setattr(
        keyphrase_llm, "_generate", lambda prompt, model, base_url: '["beta", "alpha"]'
    )
    await _seed(both_pool, TENANT, texts={_AA: "alpha beta text", _BB: "alpha beta text"})
    envelope = await keyphrase_tools.keyphrases_extract(both_pool, {}, TENANT, variant="llm")
    _assert_contract("07", envelope, "keyphrases")
    assert [row["phrase"] for row in envelope["keyphrases"]] == ["beta", "alpha"]
    assert all(row["score"] == 0.0 for row in envelope["keyphrases"])


async def _sentiment_envelope(pool: Any) -> dict[str, Any]:
    """One seeded corpus and one screen run, shared by the 26/27 rows."""
    from corpus_kb.tools import sentiment_tools

    config = {"analysis": {"sentiment": {"enabled": True}}}
    await _seed(
        pool,
        TENANT,
        texts={
            _AA: "a wonderful and brilliant report",
            _BB: "a terrible and awful outcome",
            _CC: "a wonderful result but a terrible process",
        },
    )
    return await sentiment_tools.sentiment_screen(pool, config, TENANT)


async def test_26_sentiment_screen_contract(both_pool: Any) -> None:
    """Lexicon votes: rows by chunk id; the tied chunk routes to review."""
    envelope = await _sentiment_envelope(both_pool)
    _assert_contract("26", envelope, "predictions")
    by_id = {row["chunk_id"]: row for row in envelope["predictions"]}
    assert by_id[_AA]["label"] == "positive"
    assert by_id[_BB]["label"] == "negative"
    assert by_id[_CC]["review"] is True  # the declared tie route


async def test_27_sentiment_rows_match_the_same_screen(both_pool: Any) -> None:
    """Method 27 declares the screen's rows; the caveat names the gap."""
    envelope = await _sentiment_envelope(both_pool)
    _assert_contract("27", envelope, "predictions")
    assert any("aspect" in caveat for caveat in _table("27").caveats or [])


async def test_28_contradictions_screen_contract(both_pool: Any, monkeypatch: Any) -> None:
    """Canned CONTRADICT verdicts: pairs order by the lower then higher id."""
    import corpus_kb.analysis.nli_contract as nli_contract_module
    from corpus_kb.tools import nli_tools

    async def canned(prompt: str, model: str, base_url: str) -> str:
        return "CONTRADICT"

    monkeypatch.setattr(nli_contract_module, "judge_verdict", canned)
    await _seed(both_pool, TENANT, {_AA: _vec(1.0, 0.0), _BB: _vec(0.0, 1.0), _CC: _vec(1.0, 1.0)})
    envelope = await nli_tools.contradictions_screen(both_pool, {}, TENANT, candidate_k=2)
    _assert_contract("28", envelope, "pairs")
    pairs = [(row["chunk_id_i"], row["chunk_id_j"]) for row in envelope["pairs"]]
    assert pairs == sorted(set(pairs))  # deduped candidates in id order
    assert all(row["verdict"] == "CONTRADICT" for row in envelope["pairs"])


async def test_20_active_learn_next_contract(both_pool: Any) -> None:
    """Margin ranking: equal probabilities tie and order by chunk id."""
    from corpus_kb.tools import active_learn_tools

    await _seed(both_pool, TENANT, texts={_AA: "text aa", _BB: "text bb", _CC: "text cc"})
    await _seed_predictions(
        both_pool,
        TENANT,
        [(_AA, "pos", 0.9, False), (_BB, "pos", 0.9, True), (_CC, "neg", 0.3, False)],
    )
    envelope = await active_learn_tools.active_learn_next(both_pool, {}, TENANT, strategy="margin")
    _assert_contract("20", envelope, "ranked")
    ranked = envelope["ranked"]
    assert [row["chunk_id"] for row in ranked] == [_CC, _AA, _BB]
    assert ranked[0]["score"] == round(1.0 - 0.3, 6)
    assert ranked[1]["score"] == ranked[2]["score"] == 0.1  # the crafted tie
    assert ranked[2]["review"] is True


# ---------------------------------------------------------------------------
# Cross-checks that keep the declared set honest


def test_every_declared_method_appears_in_this_gate() -> None:
    """The declared set is exactly the fourteen methods this gate pins."""
    declared = {row.id for row in load_method_register({}).methods if row.output_table is not None}
    assert declared == {
        "05",
        "06",
        "07",
        "12",
        "13",
        "14",
        "15",
        "18",
        "20",
        "26",
        "27",
        "28",
        "29",
        "30",
    }


def test_vector_seed_helper_is_width_exact() -> None:
    """The seeded literal is 2560 wide and parses as JSON."""
    assert len(_vec(1.0, 2.0)) == _WIDTH
    assert json.loads(_literal(_vec(1.0))) == [1.0] + [0.0] * (_WIDTH - 1)

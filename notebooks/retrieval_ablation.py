import marimo

__generated_with = "0.15.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import asyncio

    import marimo as mo

    return asyncio, mo


@app.cell
def _(mo):
    mo.md(
        """
        # Retrieval ablation

        Runs one question through the three retrieval arms and shows how
        the rankings differ:

        - **FTS** - Postgres full-text search with `ts_rank` scoring.
        - **Vector** - pgvector cosine similarity over stored embeddings.
        - **RRF** - the production fusion of both arms (what `search` uses).

        A question the two arms rank differently is where fusion earns its
        keep. Ingest a few documents with the configured embedder first.
        """
    )
    return


@app.cell
def _(asyncio):
    import inspect
    import json

    from corpus_kb.config import load_config
    from corpus_kb.rag import create_embedder
    from corpus_kb.server_wiring import initialize_postgres_pool
    from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID
    from corpus_kb.tools.search_tools import search

    K = 10


    def run_arms(question):
        async def _one():
            cfg = load_config()
            pool = await initialize_postgres_pool(
                str(cfg["database"]["connection_string"])
            )
            try:
                fts = await pool.fetch(FTS_SQL, question, DEFAULT_TENANT_ID, K)
                embedder = create_embedder(cfg, pool)
                query_vector = embedder.embed(question)
                if inspect.isawaitable(query_vector):
                    query_vector = await query_vector
                vector = await pool.fetch(
                    VECTOR_SQL, json.dumps(query_vector), DEFAULT_TENANT_ID, K
                )
                fused = await search(pool, question, cfg, k=K)
                return list(fts), list(vector), fused
            finally:
                await pool.close()

        return asyncio.run(_one())


    FTS_SQL = """
    SELECT chunks.chunk_id::text, left(chunks.text, 80) AS text,
           documents.source, ts_rank(to_tsvector('english', chunks.text),
           plainto_tsquery('english', $1)) AS score
    FROM chunks JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE chunks.tenant_id = $2::uuid AND chunks.tombstoned_at IS NULL
      AND to_tsvector('english', chunks.text) @@ plainto_tsquery('english', $1)
    ORDER BY score DESC, chunks.chunk_id ASC LIMIT $3
    """

    VECTOR_SQL = """
    SELECT chunks.chunk_id::text, left(chunks.text, 80) AS text,
           documents.source, 1 - (chunks_vectors.embedding <=> $1::halfvec) AS score
    FROM chunks_vectors
    JOIN chunks ON chunks.chunk_id = chunks_vectors.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE chunks_vectors.tenant_id = $2::uuid
      AND chunks_vectors.embedding IS NOT NULL AND NOT chunks_vectors.degraded
    ORDER BY score DESC, chunks.chunk_id ASC LIMIT $3
    """

    return K, run_arms


@app.cell
def _(mo):
    question_box = mo.ui.text(
        value="how does startup work", label="Question", full_width=True
    )
    question_box
    return (question_box,)


@app.cell
def _(mo, question_box, run_arms):
    fts_rows, vector_rows, fused = run_arms(question_box.value)

    def ranks(rows):
        return {str(row["chunk_id"]): index for index, row in enumerate(rows, start=1)}


    fts_rank = ranks(fts_rows)
    vector_rank = ranks(vector_rows)


    def arm_rows():
        columns = []
        for rank, row in enumerate(fused.get("result") or [], start=1):
            chunk_id = str(row.get("chunk_id", ""))
            columns.append(
                {
                    "rrf_rank": rank,
                    "chunk_id": chunk_id[:8],
                    "fts_rank": fts_rank.get(chunk_id, "-"),
                    "vector_rank": vector_rank.get(chunk_id, "-"),
                }
            )
        return columns


    mo.ui.table(arm_rows(), selection="none")
    return (arm_rows, fts_rank, fused, vector_rank)


@app.cell
def _(fts_rank, fused, mo, vector_rank):
    fused_ids = {
        str(row.get("chunk_id", "")) for row in (fused.get("result") or [])
    }
    overlap_fts = sum(1 for chunk_id in fused_ids if chunk_id in fts_rank)
    overlap_vector = sum(1 for chunk_id in fused_ids if chunk_id in vector_rank)
    mo.md(
        f"Of {len(fused_ids)} fused results, {overlap_fts} came from the FTS arm "
        f"and {overlap_vector} from the vector arm. Runners-up the fusion "
        "dropped never appear here - that is the RRF trade in one table."
    )
    return


if __name__ == "__main__":
    app.run()

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
        # Explore topics

        Lists the topics built by `corpus-kb topics build` over your stored
        chunk embeddings. Every row shows the label, size, and the top
        keywords. Requires a running database with at least one topic build.
        """
    )
    return


@app.cell
def _(asyncio):
    from corpus_kb.config import load_config
    from corpus_kb.server_wiring import initialize_postgres_pool
    from corpus_kb.tools.topics_tools import topics_list

    DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


    def fetch_topics():
        async def _run():
            cfg = load_config()
            pool = await initialize_postgres_pool(
                str(cfg["database"]["connection_string"])
            )
            try:
                return await topics_list(pool, cfg, tenant_id=DEFAULT_TENANT_ID)
            finally:
                await pool.close()

        return asyncio.run(_run())


    topics_envelope = fetch_topics()
    return (topics_envelope,)


@app.cell
def _(mo, topics_envelope):
    rows = [
        {
            "topic": topic.get("reduced_label") or topic.get("label"),
            "size": topic.get("size"),
            "keywords": ", ".join(
                str(keyword[0]) for keyword in (topic.get("keywords") or [])[:5]
            ),
        }
        for topic in (topics_envelope.get("topics") or [])
    ]
    mo.ui.table(rows, selection="none")
    return (rows,)


@app.cell
def _(mo, rows, topics_envelope):
    note = str(topics_envelope.get("status", "unavailable"))
    total = sum(int(row["size"] or 0) for row in rows)
    mo.md(
        f"Status: **{note}** - {len(rows)} topics covering {total} chunks.\n\n"
        "No topics yet? Ingest documents, then run `corpus-kb topics build`."
    )
    return


if __name__ == "__main__":
    app.run()

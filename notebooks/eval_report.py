import marimo

__generated_with = "0.15.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    from pathlib import Path

    import marimo as mo

    return Path, json, mo


@app.cell
def _(mo):
    mo.md(
        """
        # Evaluation report

        Charts the metrics from a report written by:

        ```
        corpus-kb eval run --golden golden.jsonl --report report.json
        ```

        The retrieval section carries recall@k, MRR, and nDCG@10. Answer
        metrics appear when the golden set has reference answers and the
        `deepeval` extra is installed.
        """
    )
    return


@app.cell
def _(Path, mo):
    report_box = mo.ui.text(value="report.json", label="Report file")
    report_path = Path(str(report_box.value))
    report_exists = report_path.is_file()
    mo.hstack([report_box], justify="start")
    return (report_exists, report_path)


@app.cell
def _(json, report_exists, report_path):
    retrieval: dict = {}
    answer: dict = {}
    per_record: list = []
    if report_exists:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        result = report.get("result") or {}
        retrieval = result.get("retrieval") or {}
        answer = result.get("answer") or {}
        per_record = list(retrieval.get("per_record") or [])
    return answer, per_record, retrieval


@app.cell
def _(mo, per_record, retrieval):
    mo.md(
        f"""
        **Retrieval** - recall@{retrieval.get('k', 'k')}:
        `{retrieval.get('recall_at_k', 'n/a')}` - MRR:
        `{retrieval.get('mrr', 'n/a')}` - nDCG@10:
        `{retrieval.get('ndcg_at_10', 'n/a')}` over
        {retrieval.get('n_records', 0)} record(s).
        """
    )
    return


@app.cell
def _(mo, per_record):
    rows = [
        {
            "query": row.get("query", ""),
            "recall@k": row.get("recall_at_k", 0.0),
            "mrr": row.get("mrr", 0.0),
            "ndcg@10": row.get("ndcg_at_10", 0.0),
            "latency_ms": row.get("latency_ms", 0.0),
        }
        for row in per_record
    ]
    mo.ui.table(rows, selection="none")
    return (rows,)


@app.cell
def _(answer, mo):
    note = str(answer.get("note", ""))
    lines = [f"- {key}: {answer.get(key)}" for key in sorted(answer) if key != "note"]
    body = "\n".join(lines) if lines else "No answer metrics in this report."
    mo.md(f"**Answer metrics**\n\n{body}" + (f"\n\n{note}" if note else ""))
    return


if __name__ == "__main__":
    app.run()

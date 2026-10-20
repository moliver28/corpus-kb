"""S1 probe 5: json_set presence + jsonb_set shim semantics.

Run under BOTH the shared venv (duckdb 1.4.5) and .venv-sql15 (duckdb 1.5.6).
"""
import duckdb

print("duckdb", duckdb.__version__)


def show(label, sql):
    try:
        print(f"{label}: {duckdb.sql(sql).fetchall()}")
    except Exception as e:  # noqa: BLE001
        print(f"{label}: ERROR {type(e).__name__}: {str(e)[:200]}")


show("json_set scalar-tgt  ", """SELECT json_set('{"x":1}', '$.thresholds', '{"t":9}')""")
show("jsonb_set            ", """SELECT jsonb_set('{"x":1}', '$.thresholds', '{"t":9}')""")
show("merge_patch REPL thr ", """SELECT json_merge_patch('{"thresholds":{"a":1}}', json_object('thresholds', json('{"t":9}')))""")
show("merge_patch plain-str", """SELECT json_merge_patch('{"thresholds":{"a":1}}', '{"thresholds":{"t":9}}')""")
show("merge_patch deepkeys ", """SELECT json_merge_patch('{"thresholds":{"a":1},"keep":2}', '{"thresholds":{"t":9}}')""")
show("two-step null then mp", """SELECT json_merge_patch(json_merge_patch('{"thresholds":{"a":1},"keep":2}', '{"thresholds":null}'), '{"thresholds":{"t":9}}')""")
show("merge_patch arr repl ", """SELECT json_merge_patch('{"completed":[1,2]}', '{"completed":[3]}')""")
show("json_object top keys ", """SELECT json_object('thresholds', json('{"t":9}'))""")

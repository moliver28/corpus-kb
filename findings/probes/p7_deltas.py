"""S1 probe 7: typing/behavior deltas to encode in dialect tests (1.4.5)."""
import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()


def show(label, sql):
    try:
        cur = con.execute(sql)
        print(f"{label}: rows={cur.fetchall()} cols={[(d[0], d[1]) for d in cur.description]}")
    except Exception as e:  # noqa: BLE001
        print(f"{label}: ERROR {type(e).__name__}: {str(e)[:200]}")


show("7/2 (typeof)     ", "SELECT typeof(7/2), 7/2")
show("7/2.0 (typeof)   ", "SELECT typeof(7/2.0), 7/2.0")
show("'1.1'=1          ", "SELECT '1.1'=1")
show("typeof compare   ", "SELECT typeof('1.1'=1)")
show("intvl month=30day", "SELECT INTERVAL '1 month' = INTERVAL '30 days'")
show("intvl month-days ", "SELECT INTERVAL '1 month' - INTERVAL '30 days'")
show("typeof interval  ", "SELECT typeof(INTERVAL '1 month')")
show("date_trunc timestz", "SELECT typeof(date_trunc('day', TIMESTAMPTZ '2024-01-02 03:04:05+00'))")
show("date_trunc ts    ", "SELECT typeof(date_trunc('day', TIMESTAMP '2024-01-02 03:04:05'))")
show("date_trunc date  ", "SELECT typeof(date_trunc('day', DATE '2024-01-02'))")
show("SELECT 1 WHERE f ", "SELECT 1 WHERE false")
show("WHERE false desc ", "SELECT 1 AS x WHERE false")
# informational: what does duckdb report as the type of the empty result set?
cur = con.execute("SELECT 1::INT AS x WHERE false")
print("WHERE false metadata:", [(d[0], d[1]) for d in cur.description], "rowcount:", len(cur.fetchall()))

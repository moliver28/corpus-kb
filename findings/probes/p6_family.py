"""S1 probe 6: Postgres-family feature confirmation on 1.4.5."""
import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()
con.execute("CREATE TABLE kv(k VARCHAR PRIMARY KEY, v INT)")
con.execute("INSERT INTO kv VALUES ('a', 1), ('b', 2)")

try:
    r = con.execute(
        "INSERT INTO kv VALUES ('a', 9) ON CONFLICT(k) DO UPDATE SET v = excluded.v RETURNING k, v"
    ).fetchall()
    print("ON CONFLICT..RETURNING:", r)
except Exception as e:  # noqa: BLE001
    print("ON CONFLICT..RETURNING ERROR:", type(e).__name__, e)
print("post upsert rows     :", con.execute("SELECT * FROM kv ORDER BY k").fetchall())

con.execute("CREATE TABLE dup(g VARCHAR, n INT)")
con.execute("INSERT INTO dup VALUES ('x', 2), ('x', 1), ('y', 5)")
try:
    print("DISTINCT ON          :", con.execute("SELECT DISTINCT ON (g) g, n FROM dup ORDER BY g, n").fetchall())
except Exception as e:  # noqa: BLE001
    print("DISTINCT ON ERROR    :", type(e).__name__, e)
try:
    print("FILTER (WHERE)       :", con.execute(
        "SELECT sum(n) FILTER (WHERE n > 1), sum(n) FROM dup"
    ).fetchall())
except Exception as e:  # noqa: BLE001
    print("FILTER ERROR         :", type(e).__name__, e)
try:
    print("ILIKE                :", con.execute("SELECT 'HELLO' ILIKE 'hel%'").fetchall())
except Exception as e:  # noqa: BLE001
    print("ILIKE ERROR          :", type(e).__name__, e)
try:
    print("gen_random_uuid()    :", con.execute("SELECT gen_random_uuid(), typeof(gen_random_uuid())").fetchall())
except Exception as e:  # noqa: BLE001
    print("gen_random_uuid ERROR:", type(e).__name__, e)

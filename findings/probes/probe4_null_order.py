"""Spike 4: default_null_order accepted values + DESC ordering of NULL vs NaN vs numbers."""
import duckdb

con = duckdb.connect(":memory:")

print("=== accepted values probe ===")
candidates = [
    "nulls_first", "nulls_last", "nulls_first_on_asc_last_on_desc", "nulls_last_on_asc_first_on_desc",
    "nulls_last_on_asc_last_on_desc", "nulls_first_on_asc_first_on_desc",
    "NULLS_LAST_ON_ASC_FIRST_ON_DESC", "NULLS_FIRST", "NULLS_LAST", "garbage_value", "auto",
]
for v in candidates:
    try:
        con.execute("SET default_null_order = ?", [v])
        cur = con.execute("SELECT current_setting('default_null_order')").fetchone()[0]
        print(f"  {v!r:45} ACCEPTED (stored as {cur!r})")
    except Exception as e:
        print(f"  {v!r:45} REJECTED: {str(e)[:120]}")

# reset to default for ordering demos
con.execute("SET default_null_order = 'nulls_last_on_asc_first_on_desc'")

print("\n=== current default on fresh connection ===")
con2 = duckdb.connect(":memory:")
print("fresh default:", con2.execute("SELECT current_setting('default_null_order')").fetchone())

print("\n=== DESC ordering: NULL vs NaN vs numbers (default setting) ===")
con2.execute("CREATE TABLE t(x DOUBLE)")
con2.executemany("INSERT INTO t VALUES (?)", [(None,), (float('nan'),), (3.0,), (1.0,), (None,), (2.5,)])
print("  default  DESC:", con2.execute("SELECT x FROM t ORDER BY x DESC").fetchall())
print("  default  ASC :", con2.execute("SELECT x FROM t ORDER BY x ASC").fetchall())

print("\n=== Postgres parity target: DESC = NULLS FIRST ===")
con2.execute("SET default_null_order = 'nulls_first_on_asc_last_on_desc'")
print("  nfoald DESC:", con2.execute("SELECT x FROM t ORDER BY x DESC").fetchall())
print("  nfoald ASC :", con2.execute("SELECT x FROM t ORDER BY x ASC").fetchall())

con2.execute("SET default_null_order = 'nulls_last_on_asc_first_on_desc'")
print("  nloafd DESC:", con2.execute("SELECT x FROM t ORDER BY x DESC").fetchall())
print("  nloafd ASC :", con2.execute("SELECT x FROM t ORDER BY x ASC").fetchall())

print("\n=== does explicit NULLS FIRST/LAST in the query override default_null_order? ===")
con2.execute("SET default_null_order = 'nulls_last_on_asc_first_on_desc'")
print("  DESC NULLS FIRST explicit:", con2.execute("SELECT x FROM t ORDER BY x DESC NULLS FIRST").fetchall())

print("\n=== NaN vs NULL distinctness in DESC under each setting (sanity: NaN sorts as number) ===")
con2.execute("SET default_null_order = 'nulls_first_on_asc_last_on_desc'")
print("  values only DESC:", con2.execute("SELECT x FROM t WHERE x IS NOT NULL ORDER BY x DESC").fetchall())

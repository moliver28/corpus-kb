"""S1 probe 3: JSON round-trip key order + GROUP BY semantics (1.4.5)."""
import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()
con.execute("CREATE TABLE t(j JSON)")
con.execute("""INSERT INTO t VALUES ('{"b":1,"a":2}')""")
print("raw readback      :", con.execute("SELECT j FROM t").fetchall())
print("typeof(j)         :", con.execute("SELECT typeof(j) FROM t").fetchall())
print("json() wrap       :", con.execute("""SELECT json('{"b":1,"a":2}')""").fetchall())
print("merge_patch '{}'  :", con.execute("""SELECT json_merge_patch('{"b":1,"a":2}', '{}')""").fetchall())
print("merge_patch add c :", con.execute("""SELECT json_merge_patch('{"b":1,"a":2}', '{"c":3}')""").fetchall())
print("to_json(json_col) :", con.execute("SELECT to_json(j) FROM t").fetchall())

con.execute("""INSERT INTO t VALUES ('{"a":2,"b":1}')""")
print("GROUP BY j        :", con.execute("SELECT j, count(*) FROM t GROUP BY j").fetchall())
print("GROUP BY j::VARCHAR:", con.execute("SELECT j::VARCHAR, count(*) FROM t GROUP BY j::VARCHAR").fetchall())
# does = on JSON compare text or structure?
print("json '=' text-cmp :", con.execute("""SELECT '{"b":1,"a":2}'::JSON = '{"a":2,"b":1}'::JSON""").fetchall())
# extraction order independence for read paths
print("j->'a'            :", con.execute("SELECT j->'a' FROM t ORDER BY 1").fetchall())

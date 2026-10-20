"""S1 probe 2: statement-failure semantics inside an open transaction (1.4.5)."""
import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()
con.execute("CREATE TABLE t(i INT, s VARCHAR)")
con.execute("INSERT INTO t VALUES (1, 'one')")


def attempt(label, bad_sql):
    print(f"=== {label}: {bad_sql}")
    con.execute("BEGIN")
    try:
        con.execute(bad_sql)
        print("  bad statement: unexpectedly OK")
    except Exception as e:  # noqa: BLE001
        print(f"  bad stmt error : {type(e).__name__}: {e}")
    try:
        print("  select after   :", con.execute("SELECT count(*) FROM t").fetchall())
    except Exception as e:  # noqa: BLE001
        print(f"  select ERROR   : {type(e).__name__}: {e}")
    try:
        con.execute("INSERT INTO t VALUES (2, 'two')")
        print("  insert after   : OK")
    except Exception as e:  # noqa: BLE001
        print(f"  insert ERROR   : {type(e).__name__}: {e}")
    try:
        con.execute("COMMIT")
        print("  commit         : OK")
    except Exception as e:  # noqa: BLE001
        print(f"  commit ERROR   : {type(e).__name__}: {e}")
        try:
            con.execute("ROLLBACK")
            print("  rollback       : OK (connection usable again)")
        except Exception as e2:  # noqa: BLE001
            print(f"  rollback ERROR : {e2}")


attempt("syntax error ", "SELEC 1")
attempt("UndefinedTable", "SELECT * FROM no_such_table_xyz")
attempt("type error   ", "SELECT CAST('abc' AS INT)")
print("final table content:", con.execute("SELECT * FROM t ORDER BY i").fetchall())

# Also: does the failed statement's partial work roll back?
print("=== partial-work check: failed UPDATE after a successful INSERT")
con.execute("BEGIN")
con.execute("INSERT INTO t VALUES (3, 'three')")
try:
    con.execute("INSERT INTO t VALUES ('not-an-int', 'bad')")
except Exception as e:  # noqa: BLE001
    print(f"  second insert error: {type(e).__name__}: {e}")
con.execute("COMMIT")
print("post-commit rows:", con.execute("SELECT * FROM t ORDER BY i").fetchall())

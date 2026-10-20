"""S1 probe 1: numbered/positional/named placeholder support (duckdb 1.4.5)."""
import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()


def show(label, sql, params=None):
    try:
        cur = con.execute(sql, params) if params is not None else con.execute(sql)
        print(f"{label}: OK rows={cur.fetchall()} cols={[d[0] for d in cur.description]}")
    except Exception as e:  # noqa: BLE001
        print(f"{label}: ERROR {type(e).__name__}: {e}")


# numbered $1..$N with list args
show("$1+$2 list        ", "SELECT $1::INT + $2::INT", [5, 7])
# repeated $1 (one distinct placeholder)
show("repeated $1        ", "SELECT $1::INT + $1::INT", [5])
show("repeated $1 two-arg", "SELECT $1::INT + $1::INT", [5, 5])
# param inside LIMIT
con.execute("CREATE TABLE t AS SELECT * FROM range(10) t(i)")
show("LIMIT $1           ", "SELECT list(i) FROM (SELECT i FROM t LIMIT $1)", [3])
# param as right-hand operand of a WHERE predicate
show("WHERE i > $1       ", "SELECT list(i) FROM t WHERE i > $1", [6])
show("WHERE s LIKE $1    ", "SELECT count(*) FROM t WHERE (i::VARCHAR) LIKE $1", ["%3%"])
# ? auto-increment
show("? auto-increment   ", "SELECT ?::INT + ?::INT", [5, 7])
show("? in LIMIT         ", "SELECT list(i) FROM (SELECT i FROM t LIMIT ?)", [2])
# named $name with dict
show("$name dict         ", "SELECT $a::INT + $b::INT", {"a": 5, "b": 7})
show("$name in LIMIT     ", "SELECT list(i) FROM (SELECT i FROM t LIMIT $n)", {"n": 2})
# mixed ? and $1
show("mix ? and $1       ", "SELECT ?::INT + $1::INT", [1, 2])
# $1 out of range
show("$3 with 2 args     ", "SELECT $3::INT", [1, 2])

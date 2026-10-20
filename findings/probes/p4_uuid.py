"""S1 probe 4: UUID-as-VARCHAR round-trip and binding (1.4.5)."""
import uuid

import duckdb

print("duckdb", duckdb.__version__)
con = duckdb.connect()
con.execute("CREATE TABLE v(s VARCHAR)")
con.execute("CREATE TABLE u(id UUID)")
UP = "6BA7B810-9DAD-11D1-80B4-00C04FD430C8"
LOW = UP.lower()
con.execute("INSERT INTO v VALUES (?), (?)", [UP, LOW])
print("varchar roundtrip :", con.execute("SELECT s FROM v ORDER BY s").fetchall())
con.execute("INSERT INTO u VALUES (?), (?)", [UP, LOW])
print("uuid roundtrip    :", con.execute("SELECT id FROM u ORDER BY id").fetchall())
print("uuid::VARCHAR     :", con.execute("SELECT id::VARCHAR FROM u LIMIT 1").fetchall())
print("gen_random_uuid() :", con.execute("SELECT gen_random_uuid()").fetchall())
print("gen ::VARCHAR     :", con.execute("SELECT CAST(gen_random_uuid() AS VARCHAR)").fetchall())
uu = uuid.UUID(UP)
print("bind uuid.UUID obj :", type(uu).__name__, "-> WHERE id = ? count:", con.execute("SELECT count(*) FROM u WHERE id = ?", [uu]).fetchall())
print("bind str upper     : WHERE id = ? count:", con.execute("SELECT count(*) FROM u WHERE id = ?", [UP]).fetchall())
print("bind str braces    : WHERE id = ? count:", con.execute("SELECT count(*) FROM u WHERE id = ?", ["{" + UP + "}"]).fetchall())
try:
    print("varchar col = uuid.UUID obj:", con.execute("SELECT count(*) FROM v WHERE s = ?", [uu]).fetchall())
except Exception as e:  # noqa: BLE001
    print("varchar col = uuid.UUID obj ERROR:", type(e).__name__, str(e)[:160])
# cross: varchar holding uuid used in WHERE against uuid col
print("join v.s = u.id    :", con.execute("SELECT count(*) FROM v JOIN u ON v.s = u.id::VARCHAR").fetchall())

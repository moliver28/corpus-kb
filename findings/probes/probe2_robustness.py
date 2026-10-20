"""Spike 2 (final): query-string robustness with the 1.4 scalar-macro shape + conjunctive/stopwords."""
import duckdb
import random
import string

con = duckdb.connect(":memory:")
con.execute("LOAD fts")
con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
docs = [
    ('d1', 'the quick brown fox jumps over the lazy dog'),
    ('d2', 'hyphen-word and hyphenated terms like state-of-the-art'),
    ('d3', "it's a contractions test, it's working"),
    ('d4', '知识库是知识管理的核心 知识库存储'),
    ('d5', 'boolean AND OR NOT operators in text AND more'),
    ('d6', 'star search: asterisk wildcard test'),
    ('d7', 'a longer document about nothing in particular, just filler text for baseline scoring'),
]
con.executemany("INSERT INTO chunks VALUES (?,?)", docs)
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")

SHAPE = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1) AS score "
         "FROM chunks) sq WHERE score IS NOT NULL ORDER BY score DESC")

queries = [
    ('unclosed "quote', 'unclosed "quote'),
    ('hyphen-word', 'hyphen-word'),
    ("it's", "it's"),
    ('CJK 知识库', '知识库'),
    ('AND OR NOT', 'AND OR NOT'),
    ('empty string', ''),
    ('bare *', '*'),
    ('trailing star needle*', 'needle*'),
    ('wildcard星 on indexed doc', '知识*'),
    ('single space', ' '),
    ('only punctuation', '!?.,;:'),
]
for label, q in queries:
    try:
        rows = con.execute(SHAPE, [q]).fetchall()
        print(f"[{label!r:>22}] OK rows: {rows if rows else '(empty, no error)'}")
    except Exception as e:
        print(f"[{label!r:>22}] RAISED {type(e).__name__}: {str(e)[:200]}")

random.seed(42)
rnd = ''.join(random.choices(string.ascii_letters + string.digits + ' ', k=500))
try:
    rows = con.execute(SHAPE, [rnd]).fetchall()
    print(f"[{'500-char random':>22}] OK rows: {rows if rows else '(empty, no error)'}")
except Exception as e:
    print(f"[{'500-char random':>22}] RAISED {type(e).__name__}: {str(e)[:200]}")

# parameter binding of weird strings — does the same corpus behave differently when bound vs literal?
print("\n--- literal vs param equivalence for 'unclosed \"quote'")
try:
    lit = con.execute(SHAPE.replace("$1", "'unclosed \"quote'")).fetchall()
    par = con.execute(SHAPE, ['unclosed "quote']).fetchall()
    print("literal:", lit, "| param:", par, "| equal:", lit == par)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:200])

print("\n=== conjunctive + stopwords (duckdb-fts issue #15) ===")
def cj(q, c=1):
    sql = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1, conjunctive := %d) AS score "
           "FROM chunks) sq WHERE score IS NOT NULL" % c)
    try:
        return con.execute(sql, [q]).fetchall()
    except Exception as e:
        return f"RAISED {type(e).__name__}: {str(e)[:150]}"

print("conjunctive 'the dog'      ->", cj('the dog'), " (issue #15 PRESENT if 0 rows)")
print("conjunctive 'the quick'    ->", cj('the quick'), " (issue #15 PRESENT if only-nothing/d1-missing)")
print("conjunctive 'quick dog'    ->", cj('quick dog'), " (expect d1: both content words present)")
print("conjunctive 'fox dog'      ->", cj('fox dog'), " (expect d1)")
print("conjunctive 'the' only     ->", cj('the'), " (stopword-only query)")
print("disjunctive 'the dog'      ->", cj('the dog', 0), " (baseline)")

print("\n=== stopword handling: is 'the' in the dict? ===")
print(con.execute("SELECT count(*) FROM fts_main_chunks.dict WHERE term IN ('the','dog','quick')").fetchall())
print("stopwords table cols:", [c[0] for c in con.execute("SELECT * FROM fts_main_chunks.stopwords LIMIT 0").description])
print("tokenize('the dog'):", con.execute("SELECT fts_main_chunks.tokenize('the dog')").fetchall())

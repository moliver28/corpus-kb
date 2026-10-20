# W0 Spike Findings — SQL Surface and Typing (S1, issue #127)

Environment: shared venv duckdb **1.4.5** (Windows, Git Bash); cross-checks on
duckdb **1.5.6** in worktree `.venv-sql15`. Probe scripts + raw outputs in
`findings/probes/` (`p1..p7`, `out_*_145.txt` / `out_*_156.txt`).

---

## Spike 1 — Numbered placeholders

**VERDICT: CONFIRMED** — `$1..$N`, repeated `$1`, LIMIT params, WHERE params,
`?` auto-increment, and `$name` all work on 1.4.5; arg list must match the
count of *distinct* placeholders (`Parameter argument/count mismatch, identifiers of the excess parameters: 2`).

**EVIDENCE** (`p1_placeholders.py` / `out_p1_145.txt`):

```
$1+$2 list        : OK rows=[(12,)]          SELECT $1::INT + $2::INT, [5,7]
repeated $1       : OK rows=[(10,)]          SELECT $1::INT + $1::INT, [5]
repeated $1 two-arg: ERROR InvalidInputException: Parameter argument/count mismatch, identifiers of the excess parameters: 2
LIMIT $1          : OK rows=[([0, 1, 2],)]   ... LIMIT $1, [3]
WHERE i > $1      : OK rows=[([7, 8, 9],)]
? auto-increment  : OK rows=[(12,)]          SELECT ?::INT + ?::INT, [5,7]
? in LIMIT        : OK rows=[([0, 1],)]
$name dict        : OK rows=[(12,)]          SELECT $a::INT + $b::INT, {"a":5,"b":7}
mix ? and $1      : ERROR InvalidInputException: Parameter argument/count mismatch ...
$3 with 2 args    : ERROR ... excess parameters: 1, 2
```

**RECOMMENDATION**: the port layer can keep Postgres-style `$1..$N` SQL text
and pass Python lists unchanged. Only two rules to encode in the gateway:
(a) one list entry per distinct placeholder (repeated `$1` consumes ONE arg,
unlike some drivers), (b) never mix `?` and `$n` in one statement.

**AFFECTED-WP**: sp-gateway

---

## Spike 2 — Statement failure inside an open transaction

**VERDICT: NUANCE** — parser and catalog errors leave the transaction usable,
but a conversion (type) error aborts it:
`TransactionContext Error: Current transaction is aborted (please ROLLBACK)`;
`COMMIT` then "succeeds" while silently rolling back everything in the tx.

**EVIDENCE** (`p2_tx_fail.py` / `out_p2_145.txt`, identical on 1.5.6):

| failure class | raised as | later SELECT/INSERT | COMMIT |
|---|---|---|---|
| `SELEC 1` (syntax) | `ParserException: Parser Error: syntax error at or near "SELEC"` | OK | OK, work kept |
| `SELECT * FROM no_such_table_xyz` | `CatalogException: Catalog Error: Table with name no_such_table_xyz does not exist!` | OK | OK, work kept |
| `SELECT CAST('abc' AS INT)` | `ConversionException: Conversion Error: Could not convert string 'abc' to INT32` | ERROR `Current transaction is aborted (please ROLLBACK)` | no error, but **rolled back** |

Partial-work check: `BEGIN; INSERT (3,'three') OK; bad-typed INSERT fails;
COMMIT` → post-commit rows show `(3,'three')` is gone. The tx is fully atomic.

**RECOMMENDATION**: the plan's nested-transaction depth-counter no-op holds
for parser/catalog error classes only. A ConversionException mid-transaction
must be treated as tx-fatal: ROLLBACK and replay the unit (or use
`BEGIN ... ROLLBACK`-with-retry), never "continue after error". Map
`TransactionException` / "Current transaction is aborted" in the error
taxonomy to that recovery path.

**AFFECTED-WP**: sp-gateway, sp-dialect

---

## Spike 3 — JSON round-trip key order

**VERDICT: CONFIRMED** — DuckDB JSON stores raw text; key order survives
storage, `json()`, `to_json`, and merge-patch of untouched keys; `GROUP BY` /
`=` on a JSON column is raw **text** equality.

**EVIDENCE** (`p3_json_order.py` / `out_p3_145.txt`):

```
raw readback      : [('{"b":1,"a":2}',)]        # inserted as that string
json() wrap       : [('{"b":1,"a":2}',)]
merge_patch add c : [('{"b":1,"a":2,"c":3}',)]  # original order kept, 'c' appended
GROUP BY j        : [('{"b":1,"a":2}', 1), ('{"a":2,"b":1}', 1)]   # 2 groups, not 1
'{"b":1,"a":2}'::JSON = '{"a":2,"b":1}'::JSON  -> False
```

**RECOMMENDATION**: migration snapshots can assert byte-stable JSON
round-trips. But any dedupe/`GROUP BY`/`DISTINCT` on JSON columns keys on raw
text: re-serialized or reordered JSON will not collapse. Normalize through a
canonical form (or a deterministic `to_json(struct)` projection) before
grouping; do not rely on JSON `=` as structural equality.

**AFFECTED-WP**: sp-migrate-data, sp-dialect

---

## Spike 4 — UUID as VARCHAR

**VERDICT: CONFIRMED** — VARCHAR round-trips case exactly; the native UUID
column canonicalizes to lowercase (`UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')`
from both upper and lower input); `gen_random_uuid()` is type UUID and
`::VARCHAR` prints lowercase hyphenated.

**EVIDENCE** (`p4_uuid.py`, `p4b` / `out_p4_145.txt`, `out_p4b_145.txt`):

```
varchar roundtrip : upper stays '6BA7B810-...-00C04FD430C8', lower stays lowercase
uuid roundtrip    : both inputs -> lowercase canonical UUID
bind uuid.UUID obj -> WHERE id = ? count: 2 (works on UUID col)
bind str upper / braced '{...}' -> both match (2)
varchar col = uuid.UUID obj: matches BOTH cases (implicit cast), but on a table
  holding non-UUID strings: Conversion Error: Could not convert string 'ABC'
  to INT128 when casting from source column s
```

**RECOMMENDATION**: for the Postgres->DuckDB data port, keep PG `UUID` columns
as **VARCHAR** to preserve migration fidelity, or accept lowercase
canonicalization if native UUID is chosen. Always bind UUIDs as strings (or
`str(uuid)`) against VARCHAR columns — passing a `uuid.UUID` object against a
VARCHAR column triggers a fragile implicit cast that silently succeeds on
UUID-shaped data and throws on mixed data.

**AFFECTED-WP**: sp-migrate-data, sp-dialect

---

## Spike 5 — json_set presence + jsonb_set shim (the pin decision)

**VERDICT: REFUTED (availability) / CONFIRMED (shim)** — `json_set` does not
exist on either version: `Catalog Error: Scalar Function with name json_set does not exist!` on **both 1.4.5 and 1.5.6** (`jsonb_set` likewise). So no pin
"buys" json_set; a shim is required regardless. And `json_merge_patch`
**deep-merges** objects (RFC 7396) — it does NOT replace wholesale like
`jsonb_set`.

**EVIDENCE** (`p5_json_set.py` / `out_p5_145.txt`, `out_p5_156.txt`; `p5b` / `out_p5b_145.txt`):

```
merge_patch REPL thr : '{"thresholds":{"a":1}}' + {"thresholds":{"t":9}}
                   -> {"thresholds":{"a":1,"t":9}}     # DEEP-MERGE, not replace
merge_patch deepkeys : {"keep":2,"thresholds":{"a":1,"t":9}}  # sibling kept, nested merged
two-step null then mp: {"keep":2,"thresholds":{"t":9}}        # WHOLESALE replace works
merge_patch arr repl : {"completed":[1,2]} + {"completed":[3]} -> {"completed":[3]}  # arrays replace
```

Verified shim recipe (prepared-form, NULL literal OK, empty-target OK):

```sql
json_merge_patch(
  json_merge_patch(COALESCE(col, '{}'::JSON), json_object('key', NULL)),
  json_object('key', json($value)))
-- '{"keep":1,"thresholds":{"old":0}}' + key=thresholds, v={"t":9}
-- -> '{"keep":1,"thresholds":{"t":9}}'
```

Call-site fit (read for context):
- `src/corpus_kb/projections/research/code_projector.py:94,133,154` — all three
  replace one top-level key (`thresholds` / `keywords` / `exemplar_text_sha256`)
  with a fully-computed value -> two-step shim is exact.
- `src/corpus_kb/research/batching.py:91` — recomputes the full `completed`
  array in SQL then writes it back -> arrays replace wholesale under
  merge-patch anyway, so the same shim is exact there too.

**RECOMMENDATION**: pin **`duckdb>=1.4,<1.5`** (LTS-only). Both candidate
ranges lack `json_set`, but 1.4 and 1.5 differ in result *types* (see Spike 7:
`date_trunc('day', ...)` returns DATE on 1.4.5, TIMESTAMP on 1.5.6), and the
zero-drift program requires deterministic result typing — letting the range
float across the minor boundary makes the same SQL return different column
types. Build the **two-step json_merge_patch shim** above as one shared
Python/SQL helper (clear key with `json_object(k, NULL)`, then set), not the
single-step merge (deep-merge would corrupt `thresholds` and append-merge
`completed`). Note for later: 1.5.6 matched Postgres *better* on date_trunc;
revisit the upper bound after the port is green on LTS.

**AFFECTED-WP**: sp-duckdb-migrations (pin), sp-dialect (shim + date_trunc delta)

---

## Spike 6 — Postgres-family features on 1.4.5

**VERDICT: CONFIRMED** — all five work, identical on 1.5.6.

**EVIDENCE** (`p6_family.py` / `out_p6_145.txt`):

```
ON CONFLICT(k) DO UPDATE ... RETURNING -> [('a', 9)]   # returns updated row; table shows a=9
SELECT DISTINCT ON (g) g, n FROM dup ORDER BY g, n  -> [('x', 1), ('y', 5)]
SELECT sum(n) FILTER (WHERE n > 1), sum(n) FROM dup -> [(7, 8)]
SELECT 'HELLO' ILIKE 'hel%' -> [(True,)]
SELECT gen_random_uuid(), typeof(...) -> UUID('38d9...'), 'UUID'
```

**RECOMMENDATION**: ported SQL can keep these constructs verbatim; no
rewrites needed for upserts, per-group first-row, conditional aggregates,
case-insensitive matching, or UUID generation.

**AFFECTED-WP**: sp-dialect

---

## Spike 7 — Typing/behavior deltas to encode in dialect tests

**VERDICT: NUANCE** — three Postgres deltas confirmed on 1.4.5, one pleasant
non-delta (intervals), and one **cross-version** delta (date_trunc).

**EVIDENCE** (`p7_deltas.py` / `out_p7_145.txt`, `out_p7_156.txt`):

| expression | duckdb 1.4.5 | duckdb 1.5.6 | Postgres |
|---|---|---|---|
| `7/2` | `DOUBLE` = 3.5 | `DOUBLE` = 3.5 | `INT` = 3 (integer division) |
| `'1.1' = 1` | `True` (BOOLEAN) | `True` | error: invalid input syntax for integer |
| `INTERVAL '1 month' = INTERVAL '30 days'` | `True` (diff = 0) | `True` | `True` (no delta) |
| `typeof(date_trunc('day', TIMESTAMP))` | `DATE` | `TIMESTAMP` | `TIMESTAMP` |
| `typeof(date_trunc('day', TIMESTAMPTZ))` | `TIMESTAMP WITH TIME ZONE` | same | `timestamptz` |
| `SELECT 1 AS x WHERE false` | 0 rows; metadata `('x', INTEGER)` | same | 0 rows, integer |

**RECOMMENDATION**: dialect tests must assert: (1) integer division differs —
use `//` or `CAST` where PG semantics needed (`7/2` is 3.5 in duckdb);
(2) implicit numeric literal coercion differs (`'1.1'=1` is true, PG errors) —
don't rely on string-vs-number comparisons for validation; (3) `date_trunc`
result type differs BOTH from PG (1.4.5: DATE for TIMESTAMP input) and
across duckdb versions — cast explicitly (`CAST(date_trunc(...) AS TIMESTAMP)`)
in ported SQL; (4) empty-result metadata is stable (`WHERE false` keeps column
name+type), safe for harness snapshots; (5) interval month/days comparison
matches PG, no workaround needed.

**AFFECTED-WP**: sp-dialect, sp-duckdb-migrations

---

## Plan-changing surprises (summary)

1. **Type errors are transaction-fatal** in duckdb (parser/catalog errors are
   not). The gateway's nested-transaction depth counter must classify
   `ConversionException`/`TransactionException` as ROLLBACK-and-replay, not
   continue.
2. **`json_set` does not exist even in 1.5** — the shim is mandatory on any
   pin; use the verified two-step merge-patch recipe.
3. **`date_trunc` return type flips between 1.4.x and 1.5.x** — an unexpected
   cross-version typing delta that independently justifies the `>=1.4,<1.5`
   pin for deterministic harness output.
4. DuckDB JSON `=`/`GROUP BY` is raw-text equality — projections must
   canonicalize JSON before grouping or dedupe logic will silently split keys.

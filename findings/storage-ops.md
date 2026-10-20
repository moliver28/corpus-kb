# W0 Spike Findings — Storage & Ops (agent S3)

Environment: Windows 11 (10.0.26200, Git Bash), duckdb **1.4.5** (shared venv, read-only use), pandas 3.0.6 present, pyright 1.1.411, xenon/radon from shared venv. Probes: `findings/probes/probe1_dsn.py` … `probe7_config.py` (scratch DBs/extension copies are regenerable, gitignored).

---

## Spike 1 — DSN shapes and open errors

**VERDICT:** DSN→path conversion MUST live in the gateway. `duckdb.connect()` does NOT parse `duckdb:///` URLs — it treats the whole string as a literal filename and prepends the process CWD.

**EVIDENCE**
- `duckdb.connect("duckdb:///F:/…/a.duckdb")` on an EXISTING db → `IOException`: `IO Error: Cannot open file "<cwd>\duckdb:\\\f:\…\a.duckdb": The filename, directory name, or volume label syntax is incorrect.`
- Corrected round-trip (`probe1b_dsn_v2.py`, both slash forms, spaces, idempotent, rejects scheme-less) passes all cases. Key: normalize `\` → `/` BEFORE `urlparse`, then strip leading `/` from drive-letter paths (`^/[A-Za-z]:/`).
- Path with spaces: plain path connect **OK**; DSN-form connect **FAILS** (see above — duckdb never sees a URL). Spaces are fine once converted to a plain path; `unquote()` covers `%20` if we choose to accept it.
- `read_only=True` on MISSING file → `duckdb.IOException` (concrete type `_duckdb.IOException`, MRO `IOException→OperationalError→DatabaseError→Error`):
  `IO Error: Cannot open database "…" in read-only mode: database does not exist`
- Same process, same file, RW conn open then `read_only=True` (and reverse) → `duckdb.ConnectionException`:
  `Connection Error: Can't open a connection to same database file with a different configuration than existing connections`
- Cross-process, file locked by another process holding RW → `duckdb.IOException`, message **includes the holder's PID**:
  `IO Error: Cannot open file "…": The process cannot access the file because it is being used by another process.` + blank line + `File is already open in` + `<python.exe path> (PID 42064)`

**RECOMMENDATION**
1. Gateway accepts both plain paths and `duckdb:///` DSNs; converts DSN→plain path with the v2 function before `duckdb.connect`. Never pass the DSN string through.
2. `DatabaseUnavailableError` mapping: `IOException` + `"Cannot open database" + "read-only mode"` → missing-db; `IOException` + `"being used by another process"` / `"File is already open in"` → locked-by-other-process (parse `(PID \d+)` for the message); `ConnectionException` + `"different configuration"` → config mismatch (a bug in our caller, surface as internal error, not unavailable).
3. Add all five strings to the string bank verbatim (msg text is stable but lowercase path rendering varies — match on substrings, not full equality).

**AFFECTED-WP:** sp-gateway, sp-setup-doctor

---

## Spike 2 — In-process concurrency

**VERDICT:** Two `duckdb.connect(same_path)` in one process share ONE database instance but are separate connections with separate transactions. `con.cursor()` per thread is safe for concurrent readers + one writer. Design (asyncio.Lock around transactions + `to_thread` per call) is validated.

**EVIDENCE**
- `c1 is c2` → False, but INSERT via c1 visible to c2 immediately (no close needed). Uncommitted data NOT visible: c2 saw 2 rows while c1 had 3 in an open transaction → true MVCC isolation between connections.
- 4 reader threads (own `cursor()` each, tight `SELECT count(*)` loop) + 1 writer thread (own `cursor()`, 2000 autocommit INSERTs + 1000 in one transaction): **0 errors, final count 3000/3000**.
- Control (4 threads sharing ONE connection object, no cursor): no errors in 1 run — works in practice but is not a documented contract; don't rely on it.
- Caveat: 1 of 6 runs of this probe segfaulted **at interpreter teardown** (exit 139, after all output). 4/4 clean re-runs after. Rare destructor race under threads.

**RECOMMENDATION**
1. DuckDbGateway: one `duckdb.connect()` per gateway, `con.cursor()` per operation executed in `to_thread`; an `asyncio.Lock` serializes transactions (BEGIN..COMMIT held across awaits never shares a cursor).
2. Readers don't need the lock (cursor isolation holds under the writer), but a single lock is simpler and the bench shows DuckDB is fast enough; document the choice.
3. Harness (sp-full-parity-harness): treat occasional segfault-at-exit as environmental; if it appears in CI, close all cursors/connections explicitly before process exit and consider `os._exit(0)` after cleanup in runner scripts.

**AFFECTED-WP:** sp-gateway, sp-full-parity-harness

---

## Spike 3 — Transaction-conflict capture

**VERDICT:** In duckdb 1.4.5 Python there is **no `duckdb.TransactionConflict`** (`getattr` → None). Write-write conflict surfaces as `duckdb.TransactionException` with message `TransactionContext Error: Conflict on update!`, detected **at the UPDATE, not at COMMIT**. Parallel appends never conflict. This corrects the expected string bank entry ("Transaction conflict: could not serialize access…" is NOT what 1.4.5 emits).

**EVIDENCE**
- Exception tree (duckdb 1.4.5, all subclass `duckdb.Error`): `IOException`, `ConnectionException`, `TransactionException`, `ConstraintException`, `ConversionException`, `FatalException`, `InternalException`. Note `IOException`, `ConnectionException`, and `TransactionException` are ALL `OperationalError` subclasses — catch order matters.
- Two cursors, both BEGIN, both UPDATE `bal` on the same row, overlap forced, then COMMIT: exactly one thread got `_duckdb.TransactionException` (MRO `TransactionException→OperationalError→DatabaseError→Error`), msg `TransactionContext Error: Conflict on update!`; the second thread's error was my barrier's `BrokenBarrierError` (conflictee died before the barrier). Final bal unchanged (100) → loser rolled back, winner never committed either in this interleaving (its UPDATE never ran because the barrier broke). Conflict is raised when the second UPDATE touches a row with an uncommitted version from another transaction.
- Two parallel INSERT-append transactions (500 rows each, overlapping in time): **0 errors, 1000/1000 rows** — appends don't conflict, as documented.

**RECOMMENDATION**
1. Retry predicate (type-first, message-guarded):
   `except duckdb.TransactionException as e:` retry iff `"Conflict" in str(e)`. Keep the message guard so non-conflict transaction errors still fail fast.
2. Max attempts **5**, exponential backoff 25 ms × 2^n with ±20% jitter. Retry the whole unit (BEGIN…COMMIT), never a bare statement.
3. Do NOT add a broad `except duckdb.OperationalError` retry — it also catches `IOException` (locked file), which must map to `DatabaseUnavailableError`, not retry.

**AFFECTED-WP:** sp-gateway

---

## Spike 4 — Batch insert microbench, 2000 × FLOAT[2560] (≈20.5 MB)

**VERDICT:** Parameterized inserts (any flavor) run at ~120–130 rows/s; DataFrame registration (`con.append` or register + `INSERT … SELECT`) runs at ~2,200–2,300 rows/s — **~18× faster**. `con.append` EXISTS on duckdb 1.4.5 Python (takes a pandas DataFrame; pybind11 builtin, not signature-introspectable). The gateway batch path must go through DataFrame/Arrow registration.

**EVIDENCE** (wall clock, best of runs; python-peak via tracemalloc — C buffers under-tracked, treat as indicative only)
| Variant | Time | Rows/s |
|---|---|---|
| (a) row-by-row `execute` | 15.5–17.0 s | 118–129 |
| (b) `executemany` | 15.3–16.0 s | 125–137 |
| (c) multi-row VALUES, 2 placeholders/row, chunk 500 | ≈ same order as (b) (bisect pass inside timed region; not faster) | ~126 effective |
| (d) `con.append(table, df)` (pandas) | 0.87–0.89 s | 2,255–2,311 |
| (e) `register` + `INSERT INTO … SELECT` | 0.86–0.91 s | 2,201–2,333 |

- GOTCHA: building VALUES rows with **2561 scalar placeholders** fails at every chunk size (even 1 row): `BinderException: Binder Error: table vecs has 2 columns but 2561 values were supplied`. A `FLOAT[2560]` column must be bound as ONE Python-list parameter per row (as a/b do), not scalar-per-element.
- pandas 3.0.6 is in the shared venv; (d)/(e) depend on it (or arrow — untested here).

**RECOMMENDATION**
1. DuckDbGateway bulk vector writes: build a pandas/Arrow table in memory, register, `INSERT … SELECT` (or `con.append`). Reserve parameterized `execute`/`executemany` for small row counts (<~100 rows) and metadata tables.
2. Never generate per-element placeholders for array columns; one list param per value.
3. Memory: data volume is 20.5 MB per 2000×2560 batch; build the DataFrame in one shot, not per-row Python lists held alongside.

**AFFECTED-WP:** sp-gateway

---

## Spike 5 — Type-check / lint surface

**VERDICT:** duckdb 1.4.5 ships complete type annotations; pyright (basic) passes a clean `DuckDBPyConnection`/`cursor()/execute/fetchall` probe with **0 errors** and correctly flags a deliberate assignment error. xenon and radon grade all functions **A**.

**EVIDENCE**
- Probe `probe5_types.py` (~30 lines). Clean surface: `pyright --pythonpath <venv python> probe5_types.py` → `0 errors, 0 warnings`. With deliberate `x: int = conn.execute("SELECT 1").fetchall()` → 1 error: `Type "list[tuple[Any, ...]]" is not assignable to declared type "int" (reportAssignmentType)`.
- Stubs confirm: `duckdb.DuckDBPyConnection` is importable for annotations; `.cursor()` and `.execute()` both return `DuckDBPyConnection`; `.fetchall()` → `list[tuple[Any, ...]]` (values `Any` — narrow at the gateway edge).
- Without `--pythonpath` (or venv config), pyright reports `reportMissingImports` on `import duckdb` — CI config must point pyright at the project venv (repo `pyrightconfig.json` at root already basic-mode).
- `xenon --max-absolute A --max-modules A --max-average A probe5_types.py` → exit 0. `radon cc -s` → every function grade A (complexity 1).

**RECOMMENDATION**
1. sp-gateway code type-checks cleanly under basic pyright with no `# type: ignore`; keep conn-typed parameters annotated as `duckdb.DuckDBPyConnection`.
2. Narrow `fetchall()` results at handler edges (values arrive as `Any` inside tuples).
3. Run pyright with the venv interpreter configured (as repo CI does); probe files live outside `src/` so repo gates are unaffected.

**AFFECTED-WP:** sp-gateway, sp-full-parity-harness

---

## Spike 6 — Extension offline vendoring

**VERDICT:** Offline vendoring WORKS on 1.4.5 Windows. A fresh process with `extension_directory` pointed at a copied `~/.duckdb/extensions` tree and `autoinstall_known_extensions=false` + `autoload_known_extensions=false` loads both `fts` and `postgres` with no network. `INSTALL fts VERSION 'v1.4.5'` pinning syntax is accepted.

**EVIDENCE**
- Source layout `%USERPROFILE%/.duckdb/extensions/`: `v1.4.5\windows_amd64\{fts (45.5 MB), postgres_scanner (45.6 MB), vss (45.3 MB)}.duckdb_extension` + `.info` sidecars. NOTE: a `v1.5.6` sibling dir also exists — the cache accumulates per-engine-version dirs.
- Fresh child process, `duckdb.connect(":memory:", config={"extension_directory": <copy>, "autoinstall_known_extensions": False, "autoload_known_extensions": False})`: `LOAD fts` → OK, `LOAD postgres` → OK.
- On disk the postgres file is `postgres_scanner.duckdb_extension`; `LOAD postgres` still resolves it (internal name mapping).
- `INSTALL fts VERSION 'v1.4.5'` → accepted (no error); plain `INSTALL fts` and `FORCE INSTALL postgres` → accepted.

**RECOMMENDATION**
1. Installer/doctor (sp-setup-doctor) check: for the RUNNING duckdb version, assert `<ext_dir>/<engine-version>/<platform>/fts.duckdb_extension` and `postgres_scanner.duckdb_extension` exist (platform `windows_amd64`); a stale version dir (e.g. only `v1.5.6` present for a 1.4.5 engine) must be reported.
2. Vendor by copying the `~/.duckdb/extensions/<version>` subtree (≈137 MB for the three extensions here); wire `extension_directory` + both `*_known_extensions=false` flags in server config for hermetic/offline runs.
3. Version pinning via `INSTALL … VERSION` exists syntactically but the vendored-dir path needs no pinning; prefer vendored-dir in airgapped installs.

**AFFECTED-WP:** sp-setup-doctor, sp-gateway

---

## Spike 7 — Connect-time config

**VERDICT:** Most knobs ARE valid connect-time `config={}` keys; `enable_progress_bar` is NOT (it is session-scope: `SET` after connect). Progress bar defaults to **True even when stdout is not a tty** — server processes must disable it explicitly.

**EVIDENCE**
- ACCEPTED at `duckdb.connect(path, config={...})` on 1.4.5: `autoinstall_known_extensions`, `autoload_known_extensions`, `default_null_order='nulls_last'` (reads back `NULLS_LAST`), `default_order='desc'`, `enable_external_access`, `extension_directory`, `memory_limit='1GB'` (reads back `953.6 MiB` — GB is decimal), `max_memory`, `preserve_insertion_order`, `temp_directory`, `threads=4`, `worker_threads`.
- REJECTED at connect: `enable_progress_bar` → `InvalidInputException`: `Invalid Input Error: Could not set option "enable_progress_bar" as a global option`. But `SET enable_progress_bar = false/true` works after connect (session scope, reads back correctly).
- Default when stdout is NOT a tty (subprocess with pipe, `duckdb.__interactive__ == False`): `current_setting('enable_progress_bar')` → **True**.
- Unknown key: `InvalidInputException`: `Invalid Input Error: The following options were not recognized: <key>` — validated eagerly at connect, good fail-fast.

**RECOMMENDATION**
1. Gateway connect-config whitelist (all verified on 1.4.5): `default_null_order`, `threads`, `memory_limit`, `temp_directory`, `extension_directory`, `autoinstall_known_extensions`, `autoload_known_extensions`, `preserve_insertion_order`. Do NOT whitelist `enable_progress_bar` in `config={}`.
2. Immediately after connect, `SET enable_progress_bar = false` (server/daemon contexts) — the default True will otherwise paint progress bars into captured logs on slow queries.
3. Surface unknown-config-key errors as config validation failures at startup (message above is the string-bank entry).

**AFFECTED-WP:** sp-gateway, sp-setup-doctor

---

## String bank (exact 1.4.5 Windows texts)

| Situation | Exception (module `_duckdb`) | Message |
|---|---|---|
| read_only=True, file missing | `IOException` | `IO Error: Cannot open database "…" in read-only mode: database does not exist` |
| file locked by another process | `IOException` | `IO Error: Cannot open file "…": The process cannot access the file because it is being used by another process.` + `File is already open in\n<exe> (PID n)` |
| same file, differing config, same process | `ConnectionException` | `Connection Error: Can't open a connection to same database file with a different configuration than existing connections` |
| write-write tx conflict | `TransactionException` | `TransactionContext Error: Conflict on update!` |
| FLOAT[] bound as N scalars in VALUES | `BinderException` | `Binder Error: table vecs has 2 columns but 2561 values were supplied` |
| unknown connect config key | `InvalidInputException` | `Invalid Input Error: The following options were not recognized: <key>` |
| `enable_progress_bar` in connect config | `InvalidInputException` | `Invalid Input Error: Could not set option "enable_progress_bar" as a global option` |

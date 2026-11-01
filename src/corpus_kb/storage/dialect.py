"""Storage-dialect SQL shims (storage-portability program, section 3.2).

One frozen :class:`Dialect` per engine, injected at construction. Every
method returns a SQL fragment string. Nothing here executes SQL.

Postgres outputs are byte-identical to the SQL that runs today. The tests
pin each fragment against literals pasted from the live call sites, so the
two cannot drift. DuckDB outputs follow the W0 spike findings in
``findings/search-fts.md`` and ``findings/sql-typing.md``.

Shims, not a dialect layer: a fragment shared by two or more modules and
expressible in place lives here. A whole-statement divergence stays a
per-module twin. Portable SQL stays literal in its module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS
from corpus_kb.errors import ConfigError

POSTGRES_BACKEND = "postgres"
DUCKDB_BACKEND = "duckdb"

# Punctuation runs split into spaces. Word characters (\w covers CJK) and
# inner hyphens survive, so "hyphen-word" stays one token and a quote run
# cannot reach the FTS macro.
_FTS_PUNCT_RE = re.compile(r"[^\w\s-]+")

# Compact client-side English stopword list (snowball English function
# words). The full index list lives in fts_main_chunks.stopwords; this
# subset exists so a stopword-heavy query still yields usable terms.
# SIM905 note: the split string keeps the list compact; a list literal
# would put every word on its own line.
_FTS_WORD_TEXT = (
    "a about again all am an and any are as at be because been before being "
    "both but by can could did do does doing don down during each few for from "
    "further had has have having he her here hers him his how i if in into is "
    "it itself its just m may me might more most must my no nor not now of off "
    "on once only or other our out over own re s same shall she should so some "
    "such t than that the their them then there these they this those through "
    "to too under until up us ve very was we were what when where which while "
    "who whom why will with would you your"
)
_FTS_STOPWORDS: frozenset[str] = frozenset(_FTS_WORD_TEXT.split())


def sanitize_fts_query(query: str) -> str:
    """Normalize a lexical query before the FTS arm.

    Punctuation splits into spaces, stopwords drop, whitespace collapses.
    An empty result means no usable terms: the caller skips the arm and
    never binds an empty query. Safe on both engines; Postgres
    ``plainto_tsquery`` repeats the work server-side at no cost.
    """
    tokens = [
        token
        for token in _FTS_PUNCT_RE.sub(" ", query).split()
        if token.lower() not in _FTS_STOPWORDS
    ]
    return " ".join(tokens)


@dataclass(frozen=True)
class Dialect:
    """SQL fragment shims for one storage backend.

    ``vector_dims`` sizes the DuckDB ``FLOAT[k]`` casts (the shipped regime
    is 2560). ``vector_type`` picks the Postgres cast family for the main
    embedding column: ``halfvec`` or ``vector``.
    """

    backend: str
    vector_dims: int = EMBEDDING_DIMENSIONS
    vector_type: str = "halfvec"

    @property
    def is_duckdb(self) -> bool:
        """True when this dialect targets DuckDB."""
        return self.backend == DUCKDB_BACKEND

    def vector_distance(self, col: str, param: str) -> str:
        """Cosine-distance fragment for ORDER BY or score math.

        Postgres keeps the pgvector ``<=>`` forms in use today; ``param``
        carries its own cast from :meth:`vector_cast`. DuckDB casts the
        parameter to ``FLOAT[k]``; without the cast a plain list binds as
        ``DOUBLE[]`` and the call fails (W0-verified).
        """
        if self.is_duckdb:
            return f"array_cosine_distance({col}, {param}::FLOAT[{self.vector_dims}])"
        return f"{col} <=> {param}"

    def vector_cast(self, idx: str, dims: int) -> str:
        """Cast a parameter placeholder to the engine's vector type."""
        if self.is_duckdb:
            return f"{idx}::FLOAT[{dims}]"
        return f"{idx}::{self.vector_type}({dims})"

    def matryoshka_probe(self, col: str, dims: int) -> str:
        """Reduced-dimension probe over an embedding expression.

        Postgres reuses the ``l2_normalize(subvector(...))`` form of the
        research embed projections. DuckDB slices the array (1-based).
        """
        if self.is_duckdb:
            return f"{col}[1:{dims}]"
        return f"l2_normalize(subvector({col}, 1, {dims}))"

    def fts_match(self, col: str, param: str) -> str:
        """Lexical-match fragment.

        Postgres: ``col`` is a tsvector expression; the result is a WHERE
        predicate. DuckDB: ``col`` is the indexed id column; the result is
        the inner SELECT-list scalar of the 1.4 macro shape. The caller
        wraps the subselect and drops rows with ``WHERE score IS NOT NULL``
        (the table-function form does not exist on 1.4; W0-verified).
        """
        if self.is_duckdb:
            return f"fts_main_chunks.match_bm25({col}, {param}) AS score"
        return f"{col} @@ plainto_tsquery('english', {param})"

    def fts_rank(self, col: str, param: str) -> str:
        """Relevance-score fragment for the SELECT list.

        Postgres: ``ts_rank`` over a tsvector expression, byte-identical to
        the handler and retrieval arms. DuckDB: the bare ``match_bm25``
        call; bm25 is both match and rank there.
        """
        if self.is_duckdb:
            return f"fts_main_chunks.match_bm25({col}, {param})"
        return f"ts_rank({col}, plainto_tsquery('english', {param}))"

    def jsonb_set_shim(self, col: str, default_json: str, key: str, value_param: str) -> str:
        """Replace one top-level JSON key, wholesale like ``jsonb_set``.

        Postgres: the ``jsonb_set`` form of the projector and batching
        call sites. ``default_json`` must fit the engine (``'{}'::jsonb``
        on Postgres, ``'{}'::JSON`` on DuckDB). DuckDB: the W0-verified
        two-step merge-patch — a NULL patch deletes the key, the second
        patch sets it (``json_set`` does not exist through 1.5.6, and a
        single merge-patch would deep-merge instead of replace).
        """
        if self.is_duckdb:
            return (
                f"json_merge_patch(json_merge_patch(COALESCE({col}, {default_json}),"
                f" json_object('{key}', NULL)), json_object('{key}', json({value_param})))"
            )
        return f"jsonb_set(COALESCE({col}, {default_json}), '{{{key}}}', {value_param})"

    def advisory_lock(self, *key: str) -> str:
        """Try to take the per-key ingest lock; True when taken.

        Postgres: the held-connection ``pg_try_advisory_lock`` pair of the
        ingest pipeline, one placeholder per key joined with ``':'``.
        DuckDB: no-op that still consumes the key parameters (the engine
        rejects unused bound parameters); the real lock is the single
        writer file lock.
        """
        if not key:
            raise ConfigError("The advisory lock needs at least one key part.")
        if self.is_duckdb:
            return f"SELECT length({self._key_joined(key)}) > 0"
        return f"SELECT pg_try_advisory_lock(hashtextextended({self._key_joined(key)}, 0))"

    def advisory_unlock(self, *key: str) -> str:
        """Release the advisory lock taken by :meth:`advisory_lock`."""
        if not key:
            raise ConfigError("The advisory lock needs at least one key part.")
        if self.is_duckdb:
            return f"SELECT length({self._key_joined(key)}) > 0"
        return f"SELECT pg_advisory_unlock(hashtextextended({self._key_joined(key)}, 0))"

    @staticmethod
    def _key_joined(key: tuple[str, ...]) -> str:
        """The $1 || ':' || $2 ... key expression over positional placeholders."""
        return " || ':' || ".join(f"${i}" for i in range(1, len(key) + 1))

    def hnsw_settings(
        self, iterative_scan: str, ef_search: int, max_scan_tuples: int
    ) -> tuple[str, ...]:
        """Transaction-local pgvector scan settings.

        Postgres: the ``SET LOCAL hnsw.*`` forms of the search settings
        module; ``off`` emits nothing. DuckDB: no-op (exact scan, no HNSW).
        """
        if self.is_duckdb or iterative_scan == "off":
            return ()
        return (
            f"SET LOCAL hnsw.iterative_scan = '{iterative_scan}'",
            f"SET LOCAL hnsw.ef_search = {ef_search}",
            f"SET LOCAL hnsw.max_scan_tuples = {max_scan_tuples}",
        )

    def planner_flags(self, disable_seqscan: bool, disable_sort: bool) -> tuple[str, ...]:
        """Transaction-local planner flags, seqscan before sort.

        Postgres: the ``SET LOCAL enable_* = off`` forms of the analytics
        probes. DuckDB: no-op.
        """
        if self.is_duckdb:
            return ()
        statements: list[str] = []
        if disable_seqscan:
            statements.append("SET LOCAL enable_seqscan = off")
        if disable_sort:
            statements.append("SET LOCAL enable_sort = off")
        return tuple(statements)

    def uuid_new(self) -> str:
        """Server-side UUID generation; identical on both engines."""
        return "gen_random_uuid()"

    def zero_vector_guard(self, expr: str) -> str:
        """Predicate that drops all-zero (degraded) embeddings.

        Postgres: no SQL-side guard today — the client-side all-zero probe
        and the degraded-flag predicate own it — so the fragment is TRUE.
        DuckDB: the L2 norm via the inner product (``array_norm`` does not
        exist on 1.4; W0-verified).
        """
        if self.is_duckdb:
            return f"sqrt(array_inner_product({expr}, {expr})) > 0"
        return "TRUE"


POSTGRES = Dialect(backend=POSTGRES_BACKEND)
DUCKDB = Dialect(backend=DUCKDB_BACKEND)


def dialect_for(backend: str) -> Dialect:
    """Return the frozen dialect for ``backend`` (postgres or duckdb)."""
    if backend == POSTGRES_BACKEND:
        return POSTGRES
    if backend == DUCKDB_BACKEND:
        return DUCKDB
    raise ConfigError(f"Unknown storage backend '{backend}'. Use postgres or duckdb.")

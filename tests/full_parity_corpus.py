"""Golden corpus + scenario builders for the full-parity harness (program §12).

The corpus is BUILT here, never blobbed: seven chunks across one code file,
one markdown doc and one plain-text doc, each anchored on one distinctive
term. All ids are FIXED UUIDs, so both engine legs seed identical state,
the ``chunk_id ASC`` tie bands sort identically, and cross-engine envelope
diffs are literal byte comparisons (zero-drift rule 9).

Search expectations are grounded in the W0 spikes (findings/search-fts.md):

- DuckDB 1.4.5 serves the lexical arm from the scalar ``match_bm25`` macro
  over the client-sanitized query (``storage.dialect.sanitize_fts_query``):
  case-folded, disjunctive (conjunctive + stopword zeroes out, duckdb-fts
  #15), no wildcard or CJK segmentation (only the exact full character run
  matches), 500-char tokens and punctuation return empty rows, no crash.
- Postgres serves ``plainto_tsquery('english', ...)``: case-folded,
  stopword-folded, CONJUNCTIVE across terms, and it splits a hyphenated
  query into ANDed lexemes.

Each battery query carries a declared-equivalence tag (program §1.1):
``full`` = byte-equal envelopes after the index-mode mask; ``set`` = same
result id set and status — the per-engine rank order or fused scores differ
because the lexical arms disagree (DuckDB bm25 is disjunctive and
length-normalized; Postgres plainto_tsquery is conjunctive).
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

#: The shipped embedding regime (config default).
DIMS = 2560

#: One tenant for every leg: the session-shared Postgres research database
#: is scoped by DELETE-by-tenant before each seed, so reruns are clean and
#: the two legs always hold the same rows.
TENANT_ID = "aaa00000-0000-0000-0000-00000000f001"


def _uid(tail: str) -> str:
    return f"aaa00000-0000-0000-0000-{tail}"


#: A second, ALL-degraded tenant: every vector row is a zero vector flagged
#: degraded, so a non-zero query empties the vector arm and fires the
#: degraded-filter count probe on both engines (REASON_FILTER_EMPTY).
DEGRADED_TENANT_ID = "aaa00000-0000-0000-0000-00000000f002"
DEGRADED_DOC_ID = _uid("00000000d101")
DEGRADED_CHUNK_IDS = (_uid("00000000c101"), _uid("00000000c102"))
DEGRADED_CHUNK_TEXTS = ("a kestrel feather in the sable hall", "sable lining lost needle")


DOC_IDS: dict[str, str] = {
    "code": _uid("00000000d001"),
    "md": _uid("00000000d002"),
    "text": _uid("00000000d003"),
}

#: doc key -> (source, source_type, created_at, file_hash). Timestamps are
#: strictly increasing, so list_documents has one total order on both
#: engines (the gateway arm adds a doc_id tiebreak, the pool arm does not).
DOC_SPECS: dict[str, tuple[str, str, datetime, str]] = {
    "code": ("parity_parser.py", "code", datetime(2026, 1, 1, tzinfo=UTC), "hash-parity-code"),
    "md": ("parity_notes.md", "markdown", datetime(2026, 1, 2, tzinfo=UTC), "hash-parity-md"),
    "text": ("parity_diary.txt", "text", datetime(2026, 1, 3, tzinfo=UTC), "hash-parity-text"),
}


@dataclass(frozen=True)
class ChunkSpec:
    """One corpus chunk: fixed id, text, anchor term, provenance."""

    key: str
    doc_key: str
    chunk_index: int
    text: str
    anchor: str | None  # None -> the degraded zero-vector chunk
    heading: tuple[str, ...] | None
    file_path: str
    start_line: int | None
    end_line: int | None
    degraded: bool = False


CHUNK_SPECS: tuple[ChunkSpec, ...] = (
    ChunkSpec(
        "c_code_1",
        "code",
        0,
        "def zephyr parse stream tokens",
        "zephyr",
        None,
        "parity_parser.py",
        1,
        2,
    ),
    ChunkSpec(
        "c_code_2",
        "code",
        1,
        "QuartzLoader loads quartzite shards",
        "quartzite",
        None,
        "parity_parser.py",
        3,
        5,
    ),
    ChunkSpec(
        "c_md_1",
        "md",
        0,
        "the marlin swims fast past coral walls",
        "marlin",
        ("Field Notes",),
        "parity_notes.md",
        None,
        None,
    ),
    ChunkSpec(
        "c_md_2",
        "md",
        1,
        "obsidian walls hold the lantern keys",
        "obsidian",
        ("Field Notes", "Vault"),
        "parity_notes.md",
        None,
        None,
    ),
    ChunkSpec(
        "c_text_1",
        "text",
        0,
        "a kestrel flew over the ridge at dawn",
        "kestrel",
        None,
        "parity_diary.txt",
        None,
        None,
    ),
    ChunkSpec(
        "c_text_zero",
        "text",
        1,
        "the sable coat hung in the hall",
        None,
        None,
        "parity_diary.txt",
        None,
        None,
        degraded=True,
    ),
    ChunkSpec(
        "c_text_unicode",
        "text",
        2,
        "café terrace beside the 石庭の灯 pond",
        "café",
        None,
        "parity_diary.txt",
        None,
        None,
    ),
)

CHUNK_IDS: dict[str, str] = {
    spec.key: _uid(f"00000000c{i:03d}") for i, spec in enumerate(CHUNK_SPECS, start=1)
}

#: Graph fixture: a diamond a->b->d, a->c->d (hops, typed hops, relations).
ENTITY_IDS: dict[str, str] = {
    "e_a": _uid("00000000e001"),
    "e_b": _uid("00000000e002"),
    "e_c": _uid("00000000e003"),
    "e_d": _uid("00000000e004"),
}
ENTITY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("e_a", "A-Span", "concept"),
    ("e_b", "B-Span", "concept"),
    ("e_c", "C-Span", "idea"),
    ("e_d", "D-Span", "concept"),
)
RELATION_IDS: dict[str, str] = {
    "r1": _uid("00000000f101"),
    "r2": _uid("00000000f102"),
    "r3": _uid("00000000f103"),
    "r4": _uid("00000000f104"),
}
RELATION_SPECS: tuple[tuple[str, str, str, str, float], ...] = (
    ("r1", "e_a", "e_b", "kinds", 1.0),
    ("r2", "e_a", "e_c", "other", 1.0),
    ("r3", "e_b", "e_d", "kinds", 0.75),
    ("r4", "e_c", "e_d", "other", 1.0),
)


class ParityEmbedder:
    """Deterministic one-hot embedder over the anchor vocabulary.

    Same vectors on both legs by construction (fixed indices, values 0.0/1.0
    that are exact in half, single and double precision). Unknown text maps
    to the all-zero vector, which both engines' arms treat as the degraded
    zero-query marker.
    """

    def __init__(self, dims: int = DIMS) -> None:
        self.dims = dims
        self._index = {
            "zephyr": 11,
            "quartzite": 23,
            "marlin": 37,
            "obsidian": 41,
            "kestrel": 53,
            "café": 83,
        }

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        lowered = text.lower()
        for term, index in self._index.items():
            if term in lowered:
                vector[index] = 1.0
        return vector

    def chunk_vector(self, spec: ChunkSpec) -> list[float]:
        """The seeded embedding: one-hot anchor, or zeros when degraded."""
        return [0.0] * self.dims if spec.anchor is None else self.embed(spec.text)


# --- Search scenarios --------------------------------------------------------


@dataclass(frozen=True)
class BatteryCase:
    """One adversarial query with its declared cross-engine equivalence."""

    query: str
    tag: str  # "full" | "set"
    duckdb_order: tuple[str, ...]
    pg_order: tuple[str, ...]
    status: str
    reasons: tuple[str, ...]


#: W0-grounded expected result orders (chunk keys). The zero-vector chunk
#: never appears: the shipped default ``retrieval.exclude_degraded`` filter
#: drops it from the vector arm on both engines.
BATTERY: tuple[BatteryCase, ...] = (
    BatteryCase("", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase("   ", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase("the and of it", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase("!!!???---...", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase("*", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase("a" * 500, "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase(
        "Marlin",
        "full",
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        "success",
        (),
    ),
    BatteryCase(
        "marlin*",
        "full",
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        "success",
        (),
    ),
    BatteryCase(
        "café",
        "full",
        ("c_text_unicode", "c_code_1", "c_code_2", "c_md_1", "c_md_2", "c_text_1"),
        ("c_text_unicode", "c_code_1", "c_code_2", "c_md_1", "c_md_2", "c_text_1"),
        "success",
        (),
    ),
    BatteryCase(
        "石庭の灯",
        "full",
        ("c_text_unicode",),
        ("c_text_unicode",),
        "success",
        ("zero-query-embedding",),
    ),
    BatteryCase("石庭", "full", (), (), "degraded_empty", ("zero-query-embedding",)),
    BatteryCase(
        "walls",
        "set",
        ("c_md_2", "c_md_1"),
        ("c_md_1", "c_md_2"),
        "success",
        ("zero-query-embedding",),
    ),
    BatteryCase(
        "kestrel-like",
        "set",
        ("c_text_1", "c_code_1", "c_code_2", "c_md_1", "c_md_2", "c_text_unicode"),
        ("c_text_1", "c_code_1", "c_code_2", "c_md_1", "c_md_2", "c_text_unicode"),
        "success",
        (),
    ),
    BatteryCase(
        "marlin AND obsidian",
        "set",
        ("c_md_1", "c_md_2", "c_code_1", "c_code_2", "c_text_1", "c_text_unicode"),
        ("c_md_1", "c_md_2", "c_code_1", "c_code_2", "c_text_1", "c_text_unicode"),
        "success",
        (),
    ),
    BatteryCase(
        'the "quoted phrase" marlin',
        "set",
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        ("c_md_1", "c_code_1", "c_code_2", "c_md_2", "c_text_1", "c_text_unicode"),
        "success",
        (),
    ),
)

#: The golden family: each query hits exactly one anchor chunk, so both
#: engines rank it first and the zero-similarity tie band follows in
#: chunk_id order — byte-equal envelopes.
GOLDEN_QUERIES: tuple[str, ...] = ("zephyr", "quartzite", "marlin", "Marlin", "obsidian", "café")

GOLDEN_ANCHOR: dict[str, str] = {
    "zephyr": "c_code_1",
    "quartzite": "c_code_2",
    "marlin": "c_md_1",
    "Marlin": "c_md_1",
    "obsidian": "c_md_2",
    "café": "c_text_unicode",
}


def golden_band(anchor_key: str) -> tuple[str, ...]:
    """The visible non-anchor chunks in ``chunk_id ASC`` order."""
    visible = [spec.key for spec in CHUNK_SPECS if not spec.degraded and spec.key != anchor_key]
    return tuple(sorted(visible, key=lambda key: CHUNK_IDS[key]))


# --- The seed plan: ONE statement text, both engines --------------------------


def vector_text(vector: list[float]) -> str:
    """'[v,v,...]' with no spaces — DuckDB FLOAT cast and pgvector halfvec
    input share this shape (W0 spike 5)."""
    from corpus_kb.storage.duckdb_support import vector_literal

    return str(vector_literal(vector))


def seed_plan(json_type: str, vector_type: str) -> dict[str, list[tuple[str, tuple[object, ...]]]]:
    """The golden seed as parameterized statements, grouped per tenant.

    The SAME SQL text seeds both engines (the DuckDB gateway accepts the
    ``$n`` placeholders the search arms already use); only the cast types
    differ, injected here as ``CAST($n AS <type>)`` — ``jsonb``/``halfvec``
    on Postgres, ``JSON``/``FLOAT[2560]`` on DuckDB. The Postgres leg runs
    one RLS tenant scope per group; DuckDB has no RLS and runs them flat.
    """
    plan: dict[str, list[tuple[str, tuple[object, ...]]]] = {TENANT_ID: [], DEGRADED_TENANT_ID: []}
    for tenant in (TENANT_ID, DEGRADED_TENANT_ID):
        for table in ("relations", "entities", "chunks_vectors", "chunks", "documents"):
            plan[tenant].append((f"DELETE FROM {table} WHERE tenant_id = $1", (tenant,)))
    for key, (source, source_type, created_at, file_hash) in DOC_SPECS.items():
        plan[TENANT_ID].append(
            (
                "INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count,"
                " file_hash, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                (
                    DOC_IDS[key],
                    TENANT_ID,
                    source,
                    source_type,
                    sum(1 for spec in CHUNK_SPECS if spec.doc_key == key),
                    file_hash,
                    created_at,
                ),
            )
        )
    for spec in CHUNK_SPECS:
        plan[TENANT_ID].append(
            (
                "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
                " file_path, start_line, end_line, heading_path, degraded, created_at)"
                f" VALUES ($1, $2, $3, $4, $5, $6, $7, $8, CAST($9 AS {json_type}), $10, $11)",
                (
                    CHUNK_IDS[spec.key],
                    TENANT_ID,
                    DOC_IDS[spec.doc_key],
                    spec.chunk_index,
                    spec.text,
                    spec.file_path,
                    spec.start_line,
                    spec.end_line,
                    json.dumps(list(spec.heading)) if spec.heading else None,
                    spec.degraded,
                    DOC_SPECS[spec.doc_key][2],
                ),
            )
        )
        plan[TENANT_ID].append(
            (
                "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding,"
                f" degraded) VALUES ($1, $2, 'parity-stub', CAST($3 AS {vector_type}), $4)",
                (
                    CHUNK_IDS[spec.key],
                    TENANT_ID,
                    vector_text(ParityEmbedder().chunk_vector(spec)),
                    spec.degraded,
                ),
            )
        )
    for key, name, entity_type in ENTITY_SPECS:
        plan[TENANT_ID].append(
            (
                "INSERT INTO entities (entity_id, tenant_id, name, entity_type, metadata)"
                f" VALUES ($1, $2, $3, $4, CAST('{{}}' AS {json_type}))",
                (ENTITY_IDS[key], TENANT_ID, name, entity_type),
            )
        )
    for key, source, target, relation_type, weight in RELATION_SPECS:
        plan[TENANT_ID].append(
            (
                "INSERT INTO relations (relation_id, tenant_id, source_entity_id,"
                " target_entity_id, relation_type, weight, metadata)"
                f" VALUES ($1, $2, $3, $4, $5, $6, CAST('{{}}' AS {json_type}))",
                (
                    RELATION_IDS[key],
                    TENANT_ID,
                    ENTITY_IDS[source],
                    ENTITY_IDS[target],
                    relation_type,
                    weight,
                ),
            )
        )
    plan[DEGRADED_TENANT_ID].append(
        (
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count,"
            " file_hash, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            (
                DEGRADED_DOC_ID,
                DEGRADED_TENANT_ID,
                "parity_degraded.txt",
                "text",
                len(DEGRADED_CHUNK_IDS),
                "hash-parity-degraded",
                DOC_SPECS["text"][2],
            ),
        )
    )
    for index, (chunk_id, text) in enumerate(
        zip(DEGRADED_CHUNK_IDS, DEGRADED_CHUNK_TEXTS, strict=True)
    ):
        plan[DEGRADED_TENANT_ID].append(
            (
                "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
                " file_path, degraded, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7)",
                (
                    chunk_id,
                    DEGRADED_TENANT_ID,
                    DEGRADED_DOC_ID,
                    index,
                    text,
                    "parity_degraded.txt",
                    DOC_SPECS["text"][2],
                ),
            )
        )
        plan[DEGRADED_TENANT_ID].append(
            (
                "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding,"
                f" degraded) VALUES ($1, $2, 'parity-stub', CAST($3 AS {vector_type}), TRUE)",
                (chunk_id, DEGRADED_TENANT_ID, vector_text([0.0] * DIMS)),
            )
        )
    return plan


def digest_statements() -> list[tuple[str, str, tuple[str, ...]]]:
    """(sql, table, columns) — identical text on both engines.

    The embedding column crosses as ``CAST(embedding AS VARCHAR)`` (legal on
    both engines; pgvector routes explicit string casts through I/O
    conversion), and the tenant filter takes ``$1``.
    """
    statements = []
    for table, columns, pk in DIGEST_TABLES:
        select = ", ".join(
            "CAST(embedding AS VARCHAR)" if column == "embedding" else column for column in columns
        )
        statements.append(
            (f"SELECT {select} FROM {table} WHERE tenant_id = $1 ORDER BY {pk}", table, columns)
        )
    return statements


# --- Digest machinery (migrate_digest policy, corpus side) -------------------

DIGEST_TABLES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "documents",
        (
            "doc_id",
            "tenant_id",
            "source",
            "source_type",
            "chunk_count",
            "file_hash",
            "metadata",
            "created_at",
        ),
        "doc_id",
    ),
    (
        "chunks",
        (
            "chunk_id",
            "tenant_id",
            "doc_id",
            "chunk_index",
            "text",
            "file_path",
            "start_line",
            "end_line",
            "degraded",
            "heading_path",
            "created_at",
        ),
        "chunk_id",
    ),
    (
        "chunks_vectors",
        ("chunk_id", "tenant_id", "embedding_model", "degraded", "embedding"),
        "chunk_id",
    ),
    (
        "entities",
        ("entity_id", "tenant_id", "name", "entity_type", "source_document_id", "metadata"),
        "entity_id",
    ),
    (
        "relations",
        (
            "relation_id",
            "tenant_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "weight",
            "metadata",
        ),
        "relation_id",
    ),
)


def _canonical(value: object) -> list[object]:
    """One canonical form per logical value on both engines (the
    migrate_digest policy: UUIDs as text, one UTC zone, jsonb/json through a
    sorted parse, floats through repr)."""
    if value is None:
        return ["n"]
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, UUID):
        return ["t", str(value)]
    if isinstance(value, datetime):
        return ["t", value.astimezone(UTC).isoformat()]
    if isinstance(value, int | float):
        return ["#", repr(value)]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ("{", "["):
            try:
                return _canonical(json.loads(stripped))
            except ValueError:
                pass
        return ["t", value]
    if isinstance(value, dict):
        return ["o", sorted([key, _canonical(item)] for key, item in value.items())]
    if isinstance(value, list | tuple):
        return ["a", [_canonical(item) for item in value]]
    return ["r", repr(value)]


def half_quantize(values: list[float]) -> list[float]:
    """Round through IEEE half storage (pgvector halfvec policy)."""
    packed = struct.pack(f"{len(values)}e", *values)
    return list(struct.unpack(f"{len(values)}e", packed))


def parse_vector_text(text: str) -> list[float]:
    """'[v, v, ...]' (both engines read the digest vector column as text)."""
    body = text.strip().strip("[]")
    return [float(part) for part in body.split(",") if part.strip()]


def digest_rows(columns: tuple[str, ...], rows: list[tuple[object, ...]]) -> tuple[int, str]:
    """SHA-256 over canonical row lines, in the declared row order."""
    table_hash = hashlib.sha256()
    for row in rows:
        values = [
            half_quantize(parse_vector_text(str(value)))
            if column == "embedding" and value is not None
            else value
            for column, value in zip(columns, row, strict=True)
        ]
        table_hash.update(
            json.dumps([_canonical(value) for value in values], separators=(",", ":")).encode()
        )
    return len(rows), table_hash.hexdigest()


# --- Envelope helpers ---------------------------------------------------------


def mask_index_modes(payload: dict[str, object]) -> dict[str, object]:
    """Mask the two engine-honesty keys for cross-engine byte diffs.

    Declared divergence (program §1.1): Postgres reports ``hnsw_ann`` /
    ``gin_auto``, DuckDB ``exact_scan`` / ``fts_bm25``. Everything else in
    the envelope must be byte-equal.
    """
    masked = dict(payload)
    masked["vector_index_mode"] = "<mode>"
    masked["lexical_index_mode"] = "<mode>"
    return masked


def payload_bytes(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True)


def result_ids(payload: dict[str, object]) -> list[str]:
    rows = cast("list[dict[str, object]]", payload.get("result", []))
    return [str(row["chunk_id"]) for row in rows]


def ids_to_keys(ids: list[str]) -> tuple[str, ...]:
    by_id = {chunk_id: key for key, chunk_id in CHUNK_IDS.items()}
    return tuple(by_id[chunk_id] for chunk_id in ids)


TENANT_UUID = UUID(TENANT_ID)

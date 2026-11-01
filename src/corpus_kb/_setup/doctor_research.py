"""Read-only research-subsystem checks for ``corpus-kb doctor`` (todo-19 (a)).

Contract: every DB-backed check is guarded on the doctor's ``postgres_ok``
flag (with Postgres unreachable the whole section reports SKIPPED), and NO
check ever flips the doctor exit code. A failing check prints a runnable fix
command plus a doc link - the "not applied (run setup)" pattern extended.

The checks are also reused offline by tests: ``extras_checks`` and
``embedder_check`` are pure, and ``db_checks`` takes an open connection.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

import asyncpg

from corpus_kb.dimensions import NOMIC_EMBEDDING_DIMENSIONS, RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.projections.research._common import DEFAULT_TENANT, RESEARCH_PROJECTION_NAME
from corpus_kb.research.search_settings import (
    MIN_ITERATIVE_SCAN_VERSION,
    parse_pgvector_version,
)

DOC_GETTING_STARTED = "docs/getting-started.md"
DOC_RESEARCH = "docs/research.md"

SETUP_FIX = "corpus-kb setup"

# U40/U20: the installed pgvector extension gates iterative-scan support.
PGVECTOR_VERSION_SQL = "SELECT extversion FROM pg_extension WHERE extname = 'vector'"

# Migration file -> tables it must have created. The doctor probes to_regclass
# for each so a HALF-APPLIED install names the specific missing migration
# (todo-19 QA failure scenario) instead of a generic "not applied".
EXPECTED_TABLES: dict[str, tuple[str, ...]] = {
    "012_coding_schema.sql": (
        "code_registry",
        "codebook_versions",
        "code_keywords",
        "chunk_signals",
        "chunk_keyword_hits",
        "answer_ctx_vectors",
        "batch_status",
        "chunk_codes",
        "final_codes",
        "chunk_status",
        "doc_extraction_audit",
        "coding_gate_decisions",
    ),
    "016_research_domain.sql": (
        "research_projects",
        "research_speakers",
        "research_transcript_text",
        "research_units",
        "research_exchanges",
        "research_assignments",
        "research_signals",
        "research_runs",
        "research_reviews",
        "ingested_files",
        "embedding_cache",
    ),
    "017_inductive_engine.sql": (
        "research_observations",
        "research_proposed_codes",
        "research_noise_queue",
    ),
    "018_keyword_synthesis.sql": ("research_keyword_hits",),
}

# Optional extras (todo-13/15, Stage 1 ner): report status + install command;
# NEVER fail doctor when absent - the core pipeline and the demo run without
# them.
OPTIONAL_EXTRAS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("latechunk", ("sentence_transformers",), 'pip install -e ".[latechunk]"'),
    ("inductive", ("umap", "hdbscan"), 'pip install -e ".[inductive]"'),
    # Stage 1 gliner2 NER backend (extraction/gliner_backend.py): the import
    # probe is the punter — the backend exists but degrades cleanly when the
    # optional extra is not installed.
    ("ner", ("gliner2",), 'pip install -e ".[ner]"'),
)

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_INFO = "info"
STATUS_SKIPPED = "skipped"

DOC_RECONCILIATION = "docs/research/upgrade-reconciliation.md"


@dataclass(frozen=True)
class ResearchCheck:
    """One doctor research check with its actionable remediation.

    ``embedding_dimensions`` is the structured read of the configured
    embedding width (issue #183): the detail string stays plain English,
    machines read the field. ``None`` where the check does not probe a
    width (or the config leaves it unset).
    """

    name: str
    status: str
    detail: str
    fix: str = ""
    doc: str = ""
    embedding_dimensions: int | None = None


def surface_parity_check() -> ResearchCheck:
    """The U5/P7 MECE contract: every research/release surface reachable on
    CLI + MCP + HTTP (the documented parity allowlist aside).

    Pure and offline: reads the surface registry + the parity contract; a
    gap means a registered operation lost a transport (or a new one was
    added incompletely). Never flips the doctor exit code (doctor contract).
    """
    from corpus_kb import surface_registry as registry
    from corpus_kb import surface_registry_parity as parity

    gaps = parity.surface_coverage_gaps(registry.SURFACES, parity.TRANSPORT_ALLOWLIST)
    release_ops = sorted(
        surface.name for surface in registry.SURFACES if surface.name.startswith("release-")
    )
    if gaps:
        return ResearchCheck(
            name="surface parity (cli+mcp+http)",
            status=STATUS_FAIL,
            detail=f"{len(gaps)} operation(s) missing transports: {'; '.join(gaps)}",
            fix="wire the missing transports or extend TRANSPORT_ALLOWLIST explicitly",
            doc=DOC_RECONCILIATION,
        )
    return ResearchCheck(
        name="surface parity (cli+mcp+http)",
        status=STATUS_OK,
        detail=(
            f"all {len(registry.SURFACES)} operations covered "
            f"(allowlist: {len(parity.TRANSPORT_ALLOWLIST)}); release ops: "
            f"{', '.join(release_ops)}"
        ),
        doc=DOC_RECONCILIATION,
    )


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def extras_checks() -> list[ResearchCheck]:
    """Pure import-surface checks: scipy + the optional extras (never fail)."""
    checks = [
        ResearchCheck(
            name="numeric stack (scipy)",
            status=STATUS_OK if _module_present("scipy") else STATUS_INFO,
            detail="importable" if _module_present("scipy") else "not installed",
            fix="" if _module_present("scipy") else "required only by the optional extras below",
            doc=DOC_RESEARCH,
        )
    ]
    for extra, modules, install_cmd in OPTIONAL_EXTRAS:
        present = all(_module_present(m) for m in modules)
        checks.append(
            ResearchCheck(
                name=f"optional extra {extra}",
                status=STATUS_OK if present else STATUS_INFO,
                detail="installed" if present else "not installed (optional)",
                fix="" if present else install_cmd,
                doc=DOC_RESEARCH,
            )
        )
    return checks


def embedder_check(config: dict[str, Any], pgml_installed: bool | None) -> ResearchCheck:
    """research.embedder config sanity against the 1024-dim abstain contract.

    The research boundary requires EXACTLY 1024 dims: pgml with the extension
    absent, or an ollama model under 1024 dims, makes every research embedding
    abstain (NULL vectors, lexical-only retrieval). That is graceful but a
    deployment trap, so doctor warns with the config fix.
    """
    research_cfg = config.get("research", {}) or {}
    embedder_cfg = research_cfg.get("embedder", {}) or {}
    emb_cfg = config.get("embedding", {}) or {}
    provider = str(emb_cfg.get("provider", ""))
    model = str(emb_cfg.get("model", ""))
    try:
        dimensions = int(embedder_cfg.get("dimensions", RESEARCH_EMBEDDING_DIMENSIONS))
    except (TypeError, ValueError):
        dimensions = 0
    if dimensions != RESEARCH_EMBEDDING_DIMENSIONS:
        return ResearchCheck(
            name="research.embedder config",
            status=STATUS_FAIL,
            detail=f"research.embedder.dimensions={dimensions!r}. Promotable research "
            f"embedders must emit exactly {RESEARCH_EMBEDDING_DIMENSIONS} dims",
            fix=f"set research.embedder.dimensions: {RESEARCH_EMBEDDING_DIMENSIONS} in config.yaml",
            doc=DOC_RESEARCH,
            embedding_dimensions=dimensions or None,
        )
    if provider == "pgml" and pgml_installed is False:
        return ResearchCheck(
            name="research.embedder config",
            status=STATUS_WARN,
            detail="provider=pgml but the pgml extension is absent. All research "
            "embeddings abstain (lexical-only retrieval)",
            fix=f"set embedding.provider: ollama and embedding.model to a "
            f">={RESEARCH_EMBEDDING_DIMENSIONS}-dim model (e.g. qwen3-embedding:8b-q8_0)",
            doc=DOC_RESEARCH,
            embedding_dimensions=dimensions,
        )
    if provider == "ollama":
        try:
            emb_dims = int(emb_cfg.get("dimensions", 0))
        except (TypeError, ValueError):
            emb_dims = 0
        if 0 < emb_dims < RESEARCH_EMBEDDING_DIMENSIONS:
            return ResearchCheck(
                name="research.embedder config",
                status=STATUS_WARN,
                detail=f"model {model!r} emits {emb_dims} dims, below "
                f"{RESEARCH_EMBEDDING_DIMENSIONS}. "
                "Research embeddings abstain (never padded)",
                fix=f"set embedding.model to a >={RESEARCH_EMBEDDING_DIMENSIONS}-dim "
                "model (e.g. qwen3-embedding:8b-q8_0)",
                doc=DOC_RESEARCH,
                embedding_dimensions=emb_dims,
            )
        if emb_dims == 0:
            # Unset dimensions: the model default may still be under the
            # research width (nomic-embed-text is 768) and would abstain -
            # say so instead of reporting OK for an unverifiable config.
            return ResearchCheck(
                name="research.embedder config",
                status=STATUS_WARN,
                detail="embedding.dimensions is not set. Research needs a model with "
                f">= {RESEARCH_EMBEDDING_DIMENSIONS} dims. nomic-embed-text emits "
                f"{NOMIC_EMBEDDING_DIMENSIONS} and would abstain",
                fix="set embedding.dimensions to the model's true dimensionality",
                doc=DOC_RESEARCH,
            )
    try:
        emb_dims = int(emb_cfg.get("dimensions", dimensions))
    except (TypeError, ValueError):
        emb_dims = dimensions
    return ResearchCheck(
        name="research.embedder config",
        status=STATUS_OK,
        detail=f"provider={provider} model={model} dims={emb_dims}",
        doc=DOC_RESEARCH,
        embedding_dimensions=emb_dims,
    )


def check_pgvector_version(extversion: str | None) -> ResearchCheck:
    """U40/U20 doctor check: pgvector >= 0.8.0 enables iterative scans.

    Below 0.8.0 the ``hnsw.*`` SET LOCAL settings are disabled (emitting them
    aborts the query), so relaxed-ordering guarantees are ``not_enforced`` —
    reported as a WARN, never a silent pass.
    """
    name = "pgvector version (iterative scans)"
    if extversion is None:
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail="vector extension not installed - vector search cannot run",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    parsed = parse_pgvector_version(extversion)
    if parsed is None:
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"not_enforced - unreadable pgvector version {extversion!r}. "
            "hnsw.iterative_scan stays disabled. The system fails closed.",
            doc=DOC_RESEARCH,
        )
    if parsed >= MIN_ITERATIVE_SCAN_VERSION:
        return ResearchCheck(
            name=name,
            status=STATUS_OK,
            detail=f"v{extversion} supports hnsw.iterative_scan",
            doc=DOC_RESEARCH,
        )
    floor = ".".join(str(part) for part in MIN_ITERATIVE_SCAN_VERSION)
    return ResearchCheck(
        name=name,
        status=STATUS_WARN,
        detail=f"not_enforced - pgvector v{extversion} is older than {floor}. "
        "hnsw.iterative_scan stays disabled. Searches run plain ANN scans. "
        "Their result order is approximate.",
        fix=f"Use the Corpus-KB docker image. It ships pgvector >= {floor}. "
        "Or upgrade the extension. Then re-run corpus-kb doctor.",
        doc=DOC_RESEARCH,
    )


def _embed_probe(config: dict[str, Any]) -> list[float]:
    """Embed the fixed canary probe with the configured Ollama embedder.

    Deliberately SYNCHRONOUS (the Ollama client is sync): the canary is
    called from both async (doctor_cmd) and sync (tests) contexts, and a
    CLI diagnostic blocking the loop for one probe call is acceptable.
    Raises on unreachable/broken backends; a REACHABLE-but-degraded backend
    returns zeros, which the caller reports as ``invalid_output``.
    """
    from corpus_kb.rag.embedder import OllamaEmbedder
    from corpus_kb.rag.fake_embedder import PROBE_TEXT

    embedder = OllamaEmbedder(config)
    return embedder.embed(PROBE_TEXT)


def judgment_distribution_check(config: dict[str, Any]) -> ResearchCheck:
    """U35 doctor check: ``research.judgment_distribution.use_mean``.

    Verifies the key exists and is a bool, then reports whether the runtime
    exposes the token probabilities the mean-over-judgments mode needs.
    That exposure cannot be verified statically (it depends on the live
    coder backend), so an ENABLED key reports ``not_evaluable`` — never a
    guessed OK; disabled (the default) needs no probabilities at all.
    """
    name = "research.judgment_distribution.use_mean"
    research_cfg = config.get("research", {}) or {}
    jd_cfg = research_cfg.get("judgment_distribution", {}) or {}
    if not isinstance(jd_cfg, dict):
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail="block unreadable (not a mapping); default off applies",
            fix="set research.judgment_distribution: {use_mean: false} in config.yaml",
            doc=DOC_RESEARCH,
        )
    use_mean = jd_cfg.get("use_mean", False)
    if not isinstance(use_mean, bool):
        return ResearchCheck(
            name=name,
            status=STATUS_WARN,
            detail=f"use_mean must be a bool, got {use_mean!r}; default off applies",
            fix="set research.judgment_distribution.use_mean: true|false in config.yaml",
            doc=DOC_RESEARCH,
        )
    if not use_mean:
        return ResearchCheck(
            name=name,
            status=STATUS_OK,
            detail="off (default); judgment aggregation uses the mode and needs "
            "no token probabilities",
            doc=DOC_RESEARCH,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_WARN,
        detail="not_evaluable: use_mean is ON. The doctor cannot verify that the "
        "coder runtime exposes token probabilities. Without them, aggregation "
        "falls back to the mode.",
        fix="Check that the coder backend returns token logprobs "
        "(coding/uncertainty/tier1_logprob). Or set use_mean: false.",
        doc=DOC_RESEARCH,
    )


def zero_vector_canary(config: dict[str, Any], ollama_ok: bool | None) -> ResearchCheck:
    """All-zero-vector canary (the qwen3 zero-vector incident contract).

    Embeds a fixed probe string via the configured embedding model and FAILS
    with ``invalid_output`` when the reply is all zeros (the embedder's
    degraded-mode fallback — HTTP-level success with a useless vector).
    Never runs unless the caller confirmed Ollama is reachable; every other
    path reports ``not_evaluable`` instead of guessing.
    """
    name = "embedding zero-vector canary"
    if ollama_ok is not True:
        if ollama_ok is False:
            return ResearchCheck(
                name=name,
                status=STATUS_SKIPPED,
                detail="not_evaluable - Ollama unreachable",
                fix="start Ollama, then re-run corpus-kb doctor",
                doc=DOC_GETTING_STARTED,
            )
        return ResearchCheck(
            name=name,
            status=STATUS_SKIPPED,
            detail="not_evaluable - Ollama reachability was not probed",
            fix="re-run corpus-kb doctor",
            doc=DOC_GETTING_STARTED,
        )
    emb_cfg = config.get("embedding", {}) or {}
    model = str(emb_cfg.get("model", "nomic-embed-text"))
    try:
        vector = _embed_probe(config)
    except Exception as exc:  # canary must never break doctor
        return ResearchCheck(
            name=name,
            status=STATUS_SKIPPED,
            detail=f"not_evaluable - probe failed ({exc})",
            fix="check embedding.model is pulled (ollama pull) and reachable",
            doc=DOC_GETTING_STARTED,
        )
    if not any(vector):
        return ResearchCheck(
            name=name,
            status=STATUS_FAIL,
            detail=f"invalid_output - the probe returned an all-zero vector "
            f"(model={model}). The backend answered. It returned the "
            "degraded-mode placeholder, not a real embedding.",
            fix="Check that the model emits real embeddings (ollama run / server "
            "logs). Then re-run corpus-kb doctor.",
            doc=DOC_RESEARCH,
        )
    return ResearchCheck(
        name=name,
        status=STATUS_OK,
        detail=f"probe returned a non-zero vector (model={model}, dims={len(vector)})",
        doc=DOC_RESEARCH,
    )


def _migration_check(present_tables: set[str]) -> ResearchCheck:
    missing_by_migration = {
        migration: [t for t in tables if t not in present_tables]
        for migration, tables in EXPECTED_TABLES.items()
    }
    missing_by_migration = {m: t for m, t in missing_by_migration.items() if t}
    if not missing_by_migration:
        return ResearchCheck(
            name="coding/research migrations",
            status=STATUS_OK,
            detail="all expected tables present",
            doc=DOC_GETTING_STARTED,
        )
    detail = "; ".join(
        f"{migration} missing {', '.join(tables)}"
        for migration, tables in missing_by_migration.items()
    )
    return ResearchCheck(
        name="coding/research migrations",
        status=STATUS_FAIL,
        detail=f"not applied - {detail}",
        fix=SETUP_FIX,
        doc=DOC_GETTING_STARTED,
    )


async def _public_tables(conn: asyncpg.Connection) -> set[str]:
    """Names of the ordinary tables in the ``public`` schema."""
    rows = await conn.fetch(
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        """
    )
    return {row["relname"] for row in rows}


async def _checkpoint_counts(
    conn: asyncpg.Connection,
) -> tuple[asyncpg.Record | None, int, int, int, int]:
    """Checkpoint row plus the codebook/run/assignment/DLQ counts.

    Row reads run inside ONE transaction with the default-tenant GUC: the
    doctor connects as the configured (non-superuser) role, and FORCE RLS
    makes a GUC-less verification read return ZERO rows silently.
    """
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
        )
        checkpoint = await conn.fetchrow(
            """
            SELECT last_sequence, checkpoint_timestamp FROM projection_checkpoints
            WHERE projection_name = $1
            """,
            RESEARCH_PROJECTION_NAME,
        )
        dlq_count = await conn.fetchval("SELECT count(*) FROM projection_dlq")
        versions = await conn.fetchval("SELECT count(*) FROM codebook_versions")
        runs = await conn.fetchval("SELECT count(*) FROM research_runs")
        assignments = await conn.fetchval("SELECT count(*) FROM research_assignments")
    return (
        checkpoint,
        int(dlq_count or 0),
        int(versions or 0),
        int(runs or 0),
        int(assignments or 0),
    )


def _coding_runs_check(versions: int, runs: int, assignments: int) -> ResearchCheck:
    """Info line for the codebook and coding-run counters."""
    return ResearchCheck(
        name="codebook / coding runs",
        status=STATUS_INFO,
        detail=(f"codebook_versions={versions} coding_runs={runs} assignments={assignments}"),
        doc=DOC_GETTING_STARTED,
    )


async def _checkpoint_check(
    conn: asyncpg.Connection, checkpoint: asyncpg.Record | None
) -> ResearchCheck:
    """Lag of the research projection checkpoint behind the event head."""
    if checkpoint is None:
        return ResearchCheck(
            name="projection checkpoint",
            status=STATUS_WARN,
            detail="no research projection checkpoint (projections have never run)",
            fix="corpus-kb research demo  (or ingest a transcript)",
            doc=DOC_GETTING_STARTED,
        )
    last_sequence = int(checkpoint["last_sequence"] or 0)
    head = await _events_head(conn)
    if head is None:
        lag: int | None = None
        detail = f"checkpoint at sequence {last_sequence}; event head unknown"
    else:
        lag = head - last_sequence
        detail = (
            f"checkpoint at sequence {last_sequence}, up to date (event head {head})"
            if lag <= 0
            else f"checkpoint at sequence {last_sequence}; {lag} events awaiting projection"
        )
    stale = lag is not None and lag > 0
    return ResearchCheck(
        name="projection checkpoint",
        status=STATUS_WARN if stale else STATUS_OK,
        detail=detail,
        fix="re-run any research command (catch-up is automatic)" if stale else "",
        doc=DOC_GETTING_STARTED,
    )


def _dlq_check(dlq_count: int) -> ResearchCheck:
    """Dead-letter queue state for the projection pipeline."""
    if dlq_count:
        return ResearchCheck(
            name="projection DLQ",
            status=STATUS_WARN,
            detail=f"{dlq_count} dead-lettered events need attention",
            fix="inspect projection_dlq (projection_name, error) then re-ingest",
            doc=DOC_RESEARCH,
        )
    return ResearchCheck(
        name="projection DLQ",
        status=STATUS_OK,
        detail="0 dead-lettered events",
        doc=DOC_RESEARCH,
    )


async def db_checks(conn: asyncpg.Connection, config: dict[str, Any]) -> list[ResearchCheck]:
    """DB-backed checks on an OPEN connection (migrations/checkpoint/DLQ/counts).

    Row reads run in tenant-scoped transactions (see ``_checkpoint_counts``
    and the Stage 3 block below); every check order matches the historical
    report.
    """
    migrations = _migration_check(await _public_tables(conn))
    if migrations.status != STATUS_OK:
        # Every dependent read (checkpoint/counts/DLQ) needs the same tables;
        # on a pre-coding or half-applied database name the specific missing
        # migration ("not applied - run setup") instead of surfacing a pile of
        # relation-does-not-exist errors (todo-19 QA failure scenario).
        return [migrations]

    checkpoint, dlq_count, versions, runs, assignments = await _checkpoint_counts(conn)
    checks: list[ResearchCheck] = [migrations, _coding_runs_check(versions, runs, assignments)]
    checks.append(await _checkpoint_check(conn, checkpoint))
    checks.append(_dlq_check(dlq_count))

    # Stage 3 corpus-health checks (each self-guarded; never raise). They
    # re-read chunks/chunks_vectors, so they run in their OWN transaction
    # with the tenant GUC: FORCE RLS (migration 011) silently returns ZERO
    # rows for a GUC-less read, and the block above already closed.
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT)
        )
        checks.append(await _zero_vector_check(conn))
        checks.append(await _degraded_flag_check(conn))
        checks.append(await _ingest_runs_check(conn))
    return checks


# Stage 3 doctor additions: degraded/zero-vector corpus health. All three
# checks are self-guarded (to_regclass / information_schema) so a database
# without migration 022 reports "not applied" instead of crashing.
_ZERO_VECTOR_SQL = "SELECT count(*) FROM chunks_vectors WHERE embedding::text ~ '^\\[0(,0)*\\]$'"
DEGRADED_COLUMN_SQL = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
    "AND column_name = 'degraded'"
)
INGEST_RUNS_INCOMPLETE_SQL = "SELECT count(*) FROM ingest_runs WHERE status = 'running'"

REEMBED_FIX = "start Ollama, then run: corpus-kb ingest --reembed"


async def _zero_vector_check(conn: asyncpg.Connection) -> ResearchCheck:
    """Count stored all-zero vectors (the degraded-mode failure marker)."""
    zero_vectors = int(await conn.fetchval(_ZERO_VECTOR_SQL) or 0)
    if zero_vectors:
        return ResearchCheck(
            name="zero-vector chunks",
            status=STATUS_WARN,
            detail=f"{zero_vectors} chunk vector(s) are all zeros — invisible to "
            "vector search (degraded ingest marker)",
            fix=REEMBED_FIX,
            doc=DOC_GETTING_STARTED,
        )
    return ResearchCheck(
        name="zero-vector chunks",
        status=STATUS_OK,
        detail="0 all-zero vectors stored",
        doc=DOC_GETTING_STARTED,
    )


async def _degraded_flag_check(conn: asyncpg.Connection) -> ResearchCheck:
    """Count rows flagged degraded by the ingest pipeline (migration 022)."""
    present = int(await conn.fetchval(DEGRADED_COLUMN_SQL, ["chunks", "chunks_vectors"]) or 0)
    if present != 2:
        return ResearchCheck(
            name="degraded flags",
            status=STATUS_WARN,
            detail="migration 022 not applied - degraded flags unavailable",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    vector_degraded = int(
        await conn.fetchval("SELECT count(*) FROM chunks_vectors WHERE degraded = TRUE") or 0
    )
    chunk_degraded = int(
        await conn.fetchval("SELECT count(*) FROM chunks WHERE degraded = TRUE") or 0
    )
    if vector_degraded or chunk_degraded:
        return ResearchCheck(
            name="degraded flags",
            status=STATUS_WARN,
            detail=f"{vector_degraded} vector row(s) + {chunk_degraded} chunk row(s) "
            "flagged degraded (embeddings from a failed embedder run)",
            fix=REEMBED_FIX,
            doc=DOC_GETTING_STARTED,
        )
    return ResearchCheck(
        name="degraded flags",
        status=STATUS_OK,
        detail="0 rows flagged degraded",
        doc=DOC_GETTING_STARTED,
    )


async def _ingest_runs_check(conn: asyncpg.Connection) -> ResearchCheck:
    """Count ingest runs left incomplete (status='running') — partial ingests.

    to_regclass-guarded: pre-migration databases have no ingest_runs table
    and must report "not applied" rather than a relation-does-not-exist error.
    """
    rel = await conn.fetchval("SELECT to_regclass('public.ingest_runs')")
    if rel is None:
        return ResearchCheck(
            name="ingest runs ledger",
            status=STATUS_WARN,
            detail="ingest_runs table absent (migration 022 not applied)",
            fix=SETUP_FIX,
            doc=DOC_GETTING_STARTED,
        )
    incomplete = int(await conn.fetchval(INGEST_RUNS_INCOMPLETE_SQL) or 0)
    if incomplete:
        return ResearchCheck(
            name="ingest runs ledger",
            status=STATUS_WARN,
            detail=f"{incomplete} ingest run(s) never finished (status=running) — "
            "their documents may be missing vectors or entities",
            fix=f"{REEMBED_FIX} (or re-ingest the affected files)",
            doc=DOC_GETTING_STARTED,
        )
    return ResearchCheck(
        name="ingest runs ledger",
        status=STATUS_OK,
        detail="0 incomplete ingest runs",
        doc=DOC_GETTING_STARTED,
    )


async def _events_head(conn: asyncpg.Connection) -> int | None:
    """MAX(notification_id) of the eventsourcing events table, or None."""
    try:
        from corpus_kb.domain.application import events_table_name, get_app

        app = get_app()
        table = events_table_name(app)
    except Exception:
        return None
    rel = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
    if rel is None:
        return None
    return int(await conn.fetchval(f"SELECT COALESCE(MAX(notification_id), 0) FROM {table}") or 0)


async def research_checks(
    conn_str: str,
    config: dict[str, Any],
    extensions: dict[str, tuple[bool, str]] | None,
    ollama_ok: bool | None = None,
) -> list[ResearchCheck]:
    """Collect every research check for the doctor report (never raises).

    ``ollama_ok`` (from the doctor's reachability probe) gates the
    zero-vector canary: it only embeds when the caller CONFIRMED Ollama is
    up; anything else reports ``not_evaluable`` rather than guessing.
    """
    checks = extras_checks()
    pgml_installed = None if extensions is None else extensions.get("pgml", (False, ""))[0]
    checks.append(embedder_check(config, pgml_installed))
    checks.append(judgment_distribution_check(config))
    checks.append(surface_parity_check())
    checks.append(zero_vector_canary(config, ollama_ok))
    # Reranker-backend truth (llama.cpp recipe wave): probes /health on the
    # llama.cpp server, or the model list on Ollama — offline-safe, so it
    # runs BEFORE the database connect.
    from corpus_kb._setup.doctor_reranker import reranker_backend_check

    checks.append(await reranker_backend_check(config))
    try:
        conn = await asyncpg.connect(conn_str, timeout=3)
    except Exception as exc:
        checks.append(
            ResearchCheck(
                name="database research checks",
                status=STATUS_SKIPPED,
                detail=f"could not connect ({exc})",
                fix=SETUP_FIX,
                doc=DOC_GETTING_STARTED,
            )
        )
        return checks
    try:
        extversion = await conn.fetchval(PGVECTOR_VERSION_SQL)
        checks.append(check_pgvector_version(None if extversion is None else str(extversion)))
        checks.extend(await db_checks(conn, config))
        # Configuration truth (Wave 4C): the configured embedding dimensions
        # must match the chunks_vectors.embedding typmod — pgvector enforces the
        # exact width, so a mismatch fails every vector insert.
        from corpus_kb._setup.doctor_env import dimension_guard_check, hnsw_index_check

        checks.append(await dimension_guard_check(conn, config))
        # Build-time index options vs config (WARN-only cross-check).
        checks.append(await hnsw_index_check(conn, config))
    except Exception as exc:
        checks.append(
            ResearchCheck(
                name="database research checks",
                status=STATUS_SKIPPED,
                detail=f"checks failed ({exc})",
                fix=SETUP_FIX,
                doc=DOC_GETTING_STARTED,
            )
        )
    finally:
        await conn.close()
    return checks


def print_research_checks(checks: list[ResearchCheck]) -> None:
    """Print the research section (ASCII only for cp1252 consoles)."""
    print("\nResearch subsystem:")
    for check in checks:
        print(f"  {check.name}: {check.status.upper()} - {check.detail}")
        if check.fix:
            print(f"    fix: {check.fix}")
        if check.doc:
            print(f"    docs: {check.doc}")

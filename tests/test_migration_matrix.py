"""Postgres rows of the migration upgrade matrix (W2 compat spine).

Each row rebuilds a store at a past release snapshot on a scratch
database, runs the CURRENT migration runner to head, checks schema
parity against a control store built fresh from the current set, and
smokes a chunk insert plus a graph entity insert. Rows skip honestly
without the Postgres service (``requires_postgres``) or where the
snapshot cannot exist on this server. See migration_matrix_support.py
for the machinery and its compose-image pre-record rule.
"""

from __future__ import annotations

import os

import pytest

from corpus_kb._setup.migrate import run_migrations
from tests import migration_matrix_support as support

#: Sorted anchors (rule 9): one row per (anchor, backend). "head" is the
#: control anchor — a fresh build proves the harness itself is sound.
ANCHORS: tuple[str, ...] = ("corpus-rag-v0.6.0", "head", "u48-granular-history")


@pytest.mark.requires_postgres
@pytest.mark.parametrize("anchor", ANCHORS)
async def test_postgres_anchor_upgrades_to_head(anchor: str) -> None:
    ref = support.git_ref(anchor)
    name = f"corpus_kb_w2m{os.getpid() % 100000:05d}_{support.slug(anchor)}"
    dsn = support.pg_dsn(name)
    await support.create_scratch_db(name)
    try:
        await support.build_pg_store_at(ref, dsn)
        report = await run_migrations(dsn)
        if anchor == "head":
            assert report.applied_now == []
        assert await support.pg_tracked(dsn) == support.packaged_names("migrations")
        assert await support.pg_columns(dsn) == await support.pg_columns(await support.pg_control())
        await support.pg_smoke(dsn, anchor)
    finally:
        await support.drop_scratch_db(name)

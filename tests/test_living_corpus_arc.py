"""The canonical end-to-end arc (Living Corpus W7).

The ice-cream regression, on the live test database: two ingests of
one speaker's stance, the conflict scan calling it a revision, the
human resolve superseding the outdated statement, and the Bayesian
layer showing two belief eras with ordered posteriors. This is the
paved-path smoke too: the ingest envelope carries the profile and
strategy provenance the harness reads.

The REAL pipeline runs (partition -> chunk -> extract -> atomize ->
store -> scan -> resolve -> rebuild); only the extractor seam is a
deterministic span-locating fake (no model downloads, no Ollama).
Skips honestly when Postgres is unavailable (the pg_pool fixture).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from corpus_kb.beliefs.math_core import beta_interval
from corpus_kb.beliefs.store import BeliefStore
from corpus_kb.conflicts.store import ConflictStore
from corpus_kb.statements.store import StatementStore
from corpus_kb.tools.conflicts_tools import conflicts_list, conflicts_scan
from corpus_kb.tools.ingest_common import ingest_text

pytestmark = pytest.mark.asyncio

_TEST_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"

DOC_EARLY = "Interview, session one. I really like ice cream after dinner."
DOC_LATE = "Interview, session two. I dislike ice cream now."


class _SpanExtractor:
    """Deterministic ML-extractor double: locates 'ice cream' by span."""

    extractor_id = "ml"

    def extract(
        self, chunks: list[Any], ontology: Any, source_document_id: str
    ) -> tuple[list[Any], list[Any], Any]:
        from corpus_kb.extraction.protocol import ExtractStats
        from corpus_kb.utils.models import Entity

        entities: list[Entity] = []
        for chunk in chunks:
            start = chunk.text.find("ice cream")
            if start < 0:
                continue
            entities.append(
                Entity(
                    name="ice cream",
                    entity_type="FOOD",
                    source_type=chunk.source_type,
                    source_document_id=source_document_id,
                    chunk_id=chunk.chunk_id,
                    source_start_char=start,
                    source_end_char=start + len("ice cream"),
                    confidence=0.95,
                )
            )
        return entities, [], ExtractStats(backend=self.extractor_id)


@pytest.fixture
def span_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    import corpus_kb.tools.ingest_extract as ingest_extract

    monkeypatch.setattr(
        ingest_extract, "create_extractor", lambda config, pool=None, **kw: _SpanExtractor()
    )


def _config() -> dict[str, Any]:
    return {
        "graph": {"extract_entities": True, "backend": "postgres"},
        "database": {"connection_string": _TEST_DSN},
        "statements": {"enabled": True, "templates_dir": "config/claim_templates"},
        "extraction": {"backend": "ml"},
    }


async def test_ice_cream_belief_arc(pg_pool: Any, span_extractor: None) -> None:
    import uuid

    tenant = str(uuid.uuid4())
    config = _config()

    # 1. The paved ingest: profile provenance rides the envelope even
    #    with no profile active (None), and the strategy basis names the
    #    deciding layer.
    early = await ingest_text(
        text=DOC_EARLY,
        pg_pool=pg_pool,
        source_type="text",
        source="arc-early",
        config=dict(config),
        tenant_id=tenant,
    )
    assert early["status"] in {"success", "partial"}, early
    assert early["profile"] is None
    assert early["strategy_basis"] in {"profile", "config", "detection", "default"}

    # The arc is about TIME: the revision must read as strictly later,
    # so the two ingests never share a clock tick (Windows tick ~15 ms).
    await asyncio.sleep(0.05)
    import os as _os

    if _os.environ.get("ARC_DEBUG"):
        print("ARC early:", early["entity_count"], early["extractor_id"], early["statements"])

    late = await ingest_text(
        text=DOC_LATE,
        pg_pool=pg_pool,
        source_type="text",
        source="arc-late",
        config=dict(config),
        tenant_id=tenant,
    )
    assert late["status"] in {"success", "partial"}, late

    # 2. Both statements landed as preference atoms anchored on the
    #    in-sentence entity.
    store = StatementStore(pg_pool, tenant)
    rows = await store.list_statements(pg_pool, limit=50)
    texts = {str(row["text"]) for row in rows}
    assert any("like ice cream" in t for t in texts), texts
    assert any("dislike ice cream" in t for t in texts), texts

    # 3. The cascade: oldest-first pair, non-overlapping windows, so the
    #    verdict is a REVISION (the belief moved, it never clashed).
    flagged = await conflicts_scan(pg_pool, tenant_id=tenant)
    assert flagged["status"] == "success"
    assert int(flagged["flagged"]) >= 1, flagged
    listed = await conflicts_list(pg_pool, tenant_id=tenant, limit=50)
    revisions = [c for c in listed["conflicts"] if c["verdict"] == "revision"]
    assert len(revisions) == 1, listed
    conflict_id = str(revisions[0]["conflict_id"])

    # 4. The human resolve supersedes the EARLIER (like) statement — the
    #    scan pairs oldest-first, so statement_a is the outdated claim.
    resolved = await ConflictStore(pg_pool, tenant).resolve(
        conflict_id, resolved_verdict="revision", resolved_by="e2e-arc"
    )
    assert resolved is not None
    after = await store.list_statements(pg_pool, limit=50)
    by_text = {str(r["text"]): r for r in after}
    like_row = next(r for t, r in by_text.items() if "really like ice cream" in t)
    dislike_row = next(r for t, r in by_text.items() if "dislike ice cream" in t)
    if os.environ.get("ARC_DEBUG"):
        print("ARC resolved:", resolved)
        print("ARC like:", like_row["statement_id"], like_row.get("superseded_by"))
        print("ARC dislike:", dislike_row["statement_id"], dislike_row.get("superseded_by"))
    assert like_row.get("superseded_by") is not None, like_row
    assert dislike_row.get("superseded_by") is None, dislike_row

    # 5. The Bayesian layer: one belief, two eras, ordered intervals.
    beliefs = BeliefStore(pg_pool, tenant)
    summary = await beliefs.rebuild()
    assert summary["status"] == "success"
    current = await beliefs.current(limit=50)
    ice = [
        b
        for b in current
        if "ice cream" in str(b["object"]).lower() and str(b["relation"]) == "preference"
    ]
    assert len(ice) == 1, current
    belief = ice[0]
    assert int(belief["era_count"]) == 2, belief
    assert int(belief["evidence_count"]) == 2, belief
    alpha, beta = float(belief["alpha"]), float(belief["beta"])
    mean = alpha / (alpha + beta)
    low, high = beta_interval(alpha, beta)
    assert 0.0 <= low < mean < high <= 1.0, belief

    # 6. The timeline keeps the full history: the positive era first,
    #    the negative era second, posteriors ordered.
    history = await beliefs.timeline(
        subject=str(belief["subject"]),
        relation=str(belief["relation"]),
        object_name=str(belief["object"]),
    )
    assert history is not None
    eras = history["eras"]
    assert len(eras) == 2, eras
    assert eras[0]["positives"] == 1 and eras[0]["negatives"] == 0, eras
    assert eras[1]["positives"] == 0 and eras[1]["negatives"] == 1, eras
    assert float(eras[0]["mean"]) > 0.5 > float(eras[1]["mean"]), eras

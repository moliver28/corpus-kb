"""Fixture E2E for the governance report (todo 17) — acceptance proofs.

Requires Postgres (requires_postgres, auto-skipped by the conftest TCP
probe); runs locally on the live 5432 instance with throwaway DBs.

Seeds a TWO-source-type synthetic corpus (interview + meeting) with three
coded themes (A on c1, B on a c1/c2 blend), a promotable theme (c4), and a
SEEDED HIDDEN THEME (h — no matching code), then drives the REAL paths:

  * codebook v1 (A, B) -> promote the c4 proposal -> codebook v2 (code C);
  * keyword synthesis (Monroe log-odds) -> KeywordSetUpdated events ->
    theory.keywords + research_keyword_hits with hit_location populations;
  * ``build_report`` — the schema-pinned ResearchReport with R(tau_res)
    per source type (tau_res CALIBRATED, differs per type), coverage-curve
    slope, candidate missing codes (hidden theme DETECTED + review queue),
    bootstrap stability + DBCV, semantic overlap (seeded collision flagged),
    dual IRR (alpha + AC1), conformal block, G3 block, manifest;
  * SQL-side analytics: EXPLAIN shows the HNSW index (no seq-scan on the
    vector table).

Evidence artifact (full report + EXPLAIN proofs) lands at the env seam
CORPUS_KB_REPORT_EVIDENCE (default tmp_path) for the task evidence JSON.
"""

from __future__ import annotations

import json
import os
import uuid as uuid_mod
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pytest

pytestmark = pytest.mark.requires_postgres

DIM = 1024
N_A = 12
N_B = 12
N_H = 8
N_C4 = 8
N_GOLD_PER_DOC = 2

A_TEXT = "billing audit queue stalls every monday and the invoice totals drift"
B_TEXT = "scheduling audit desk reassigns technicians when calendars collide"
H_TEXT = "weather station remark about rainfall patterns unrelated to work"
C4_TEXT = "training cohort feedback on the new onboarding checklist"


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _orthogonalize(v: np.ndarray, against: list[np.ndarray]) -> np.ndarray:
    for base in against:
        v = v - (v @ base) * base
    norm = np.linalg.norm(v)
    return v / norm


def _blend(a: np.ndarray, b: np.ndarray, wa: float) -> np.ndarray:
    m = wa * a + (1.0 - wa) * b
    return m / np.linalg.norm(m)


def _unit_text(theme: str, doc: str, idx: int) -> str:
    base = {"A": A_TEXT, "B": B_TEXT, "H": H_TEXT, "C4": C4_TEXT}[theme]
    return f"{theme} {doc} unit{idx}: {base}"


def _embedding_256(v: np.ndarray) -> np.ndarray:
    sub = v[:256]
    return sub / np.linalg.norm(sub)


def _sql_vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in v) + "]"


class Corpus:
    """Deterministic corpus geometry for one seeded run."""

    def __init__(self) -> None:
        rng = np.random.default_rng(20261007)
        c1 = _vec(1)
        raw2 = _vec(2)
        c2 = _orthogonalize(raw2, [c1])
        raw4 = _vec(3)
        c4 = _orthogonalize(raw4, [c1, c2])
        rawh = _vec(4)
        ch = _orthogonalize(rawh, [c1, c2, c4])
        self.c1, self.c2, self.c4, self.ch = c1, c2, c4, ch
        self.b_gold = _blend(c1, c2, 0.5)  # gold B prototypes ~0.71 from c1
        self.b_run = _blend(c1, c2, 0.8)  # run members ~0.97 from c1
        self.rows: list[dict[str, Any]] = []
        unit_seq = 0
        for doc, dtype, noise in (("doc-i", "interview", 0.01), ("doc-m", "meeting", 0.06)):
            seq = 0
            for theme, center, n in (
                ("A", c1, N_A),
                ("B", self.b_run, N_B),
                ("H", ch, N_H),
                ("C4", c4, N_C4),
            ):
                for i in range(n):
                    jitter = 0.12 if (theme == "A" and dtype == "meeting") else noise
                    v = center + jitter * rng.normal(size=DIM).astype(np.float32)
                    v /= np.linalg.norm(v)
                    self.rows.append(
                        {
                            "unit_seq": unit_seq,
                            "doc": doc,
                            "doc_type": dtype,
                            "theme": theme,
                            "seq": seq,
                            "text": _unit_text(theme, doc, i),
                            "vector": v,
                            "question": (
                                f"Question {theme} {doc} {i}: tell me about the audit process"
                            ),
                        }
                    )
                    unit_seq += 1
                    seq += 1
            # gold-only B exemplars at the 0.71 blend (never assigned).
            for gi in range(N_GOLD_PER_DOC):
                v = self.b_gold + 0.01 * rng.normal(size=DIM).astype(np.float32)
                v /= np.linalg.norm(v)
                self.rows.append(
                    {
                        "unit_seq": unit_seq,
                        "doc": doc,
                        "doc_type": dtype,
                        "theme": "GOLD_B",
                        "seq": seq,
                        "text": f"GOLD_B {doc} exemplar{gi}: blend anchor for code b",
                        "vector": v,
                        "question": f"Question gold b {doc} {gi}",
                    }
                )
                unit_seq += 1
                seq += 1

    def theme_units(self, theme: str) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["theme"] == theme]


CORPUS = Corpus()


async def _seed(pool: Any, tenant_id: str) -> dict[str, Any]:
    from corpus_kb.storage.tenant_conn import tenant_connection

    doc_ids: dict[str, str] = {}
    unit_ids: dict[int, int] = {}
    text_shas: dict[int, str] = {}
    async with tenant_connection(pool, tenant_id) as conn:
        for doc, dtype in (("doc-i", "interview"), ("doc-m", "meeting")):
            doc_id = str(uuid_mod.uuid4())
            doc_ids[doc] = doc_id
            await conn.execute(
                """
                INSERT INTO documents (doc_id, tenant_id, source, source_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (tenant_id, source) DO NOTHING
                """,
                doc_id,
                tenant_id,
                f"fixture://report/{doc}",
                dtype,
            )
        for row in CORPUS.rows:
            doc_id = doc_ids[row["doc"]]
            sha = uuid_mod.uuid4().hex
            q_sha = uuid_mod.uuid4().hex
            exchange_id = await conn.fetchval(
                """
                INSERT INTO research_exchanges
                (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids, question_text,
                 qa_text, stance, term_origin, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, '{}', '{}', $4, $5, 'affirm', 'participant',
                        $6::vector, l2_normalize(subvector($6::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING exchange_id
                """,
                tenant_id,
                doc_id,
                row["seq"],
                row["question"],
                f"{row['question']} || {row['text']}",
                _sql_vec(row["vector"]),
            )
            q_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, seq, text, text_sha256, role_in_exchange,
                 exchange_id, is_codable, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, 'question', $6, FALSE, $7::vector,
                        l2_normalize(subvector($7::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                row["seq"] * 2,
                row["question"],
                q_sha,
                int(exchange_id),
                _sql_vec(row["vector"]),
            )
            a_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, seq, text, text_sha256, role_in_exchange,
                 exchange_id, is_codable, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, 'answer', $6, TRUE, $7::vector,
                        l2_normalize(subvector($7::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                row["seq"] * 2 + 1,
                row["text"],
                sha,
                int(exchange_id),
                _sql_vec(row["vector"]),
            )
            await conn.execute(
                "UPDATE research_exchanges SET q_unit_ids = $4, a_unit_ids = $5 "
                "WHERE tenant_id = $1 AND exchange_id = $2 AND doc_id = $3",
                tenant_id,
                int(exchange_id),
                doc_id,
                [int(q_id)],
                [int(a_id)],
            )
            unit_ids[row["unit_seq"]] = int(a_id)
            text_shas[row["unit_seq"]] = sha

        async def _assign(
            unit_seq: int,
            code_id: str,
            status: str = "auto",
            rationale: str = "deductive:v2:explicit",
            sim: float = 0.97,
            basis: str | None = "explicit_in_answer",
        ) -> int:
            return int(
                await conn.fetchval(
                    """
                    INSERT INTO research_assignments
                    (tenant_id, assignment_aggregate_id, unit_id, code_id,
                     cb_version_id, run_id, sim_answer, sim_qa, sim_q,
                     evidence_basis, stance, term_origin, rationale,
                     confidence, tier_fired, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $7, $8, 'affirm',
                            'participant', $9, 'high', 0, $10)
                    RETURNING assignment_id
                    """,
                    tenant_id,
                    str(uuid_mod.uuid4()),
                    unit_ids[unit_seq],
                    code_id,
                    None,
                    None,
                    sim,
                    basis,
                    rationale,
                    status,
                )
            )

        code_a = str(uuid_mod.uuid4())
        code_b = str(uuid_mod.uuid4())
        a_ids = [r["unit_seq"] for r in CORPUS.theme_units("A")]
        b_ids = [r["unit_seq"] for r in CORPUS.theme_units("B")]
        c4_ids = [r["unit_seq"] for r in CORPUS.theme_units("C4")]
        h_ids = [r["unit_seq"] for r in CORPUS.theme_units("H")]
        gold_b_ids = [r["unit_seq"] for r in CORPUS.theme_units("GOLD_B")]
        for u in a_ids:
            await _assign(u, code_a)
        for u in b_ids:
            await _assign(u, code_b)
        # multi-category membership: one A unit double-assigned to code B.
        shared = await _assign(a_ids[0], code_b, sim=0.90)
        # gray-zone review disposition on an existing A row (exclusion source).
        await conn.execute(
            """
            UPDATE research_assignments SET status = 'review',
                   rationale = 'deductive:v2:gray_zone', sim_answer = 0.55,
                   evidence_basis = NULL
            WHERE tenant_id = $1 AND unit_id = $2 AND code_id = $3
            """,
            tenant_id,
            unit_ids[a_ids[5]],
            code_a,
        )
        # review trail: 6 accepts + 2 overrides (dual IRR sheets).
        review_targets = [
            (a_ids[1], "accept"),
            (a_ids[2], "accept"),
            (a_ids[3], "accept"),
            (b_ids[1], "accept"),
            (b_ids[2], "accept"),
            (b_ids[3], "accept"),
            (b_ids[4], "override"),
            (b_ids[5], "override"),
        ]
        for u, decision in review_targets:
            row_id = await conn.fetchval(
                "SELECT assignment_id FROM research_assignments "
                "WHERE tenant_id = $1 AND unit_id = $2 LIMIT 1",
                tenant_id,
                unit_ids[u],
            )
            await conn.execute(
                """
                INSERT INTO research_reviews (tenant_id, assignment_id, reviewer, decision)
                VALUES ($1, $2, 'human', $3)
                ON CONFLICT (tenant_id, assignment_id, reviewer) DO NOTHING
                """,
                tenant_id,
                int(row_id),
                decision,
            )
        # c4 proposal (the v1 -> v2 promotion candidate).
        await conn.execute(
            """
            INSERT INTO research_proposed_codes
            (tenant_id, cluster_id, run_id, batch_no, suggested_label,
             member_unit_ids, n_members, status)
            VALUES ($1, $2, $3, 1, 'training cohort feedback', $4, $5, 'proposed')
            """,
            tenant_id,
            "t17:c4",
            str(uuid_mod.uuid4()),
            [unit_ids[u] for u in c4_ids],
            len(c4_ids),
        )
        # run history for the coverage curve + conformal block.
        await conn.execute(
            """
            INSERT INTO research_runs (run_id, tenant_id, params, state, checkpoint)
            VALUES ($1, $2, '{}', 'stopped', $3::jsonb)
            """,
            str(uuid_mod.uuid4()),
            tenant_id,
            json.dumps(
                {
                    "n_codes": 2,
                    "exhaustiveness": {"pooled": {"R": 0.70, "tau_res": 0.9}},
                }
            ),
        )
        run2 = str(uuid_mod.uuid4())
        await conn.execute(
            """
            INSERT INTO research_runs (run_id, tenant_id, params, state, checkpoint)
            VALUES ($1, $2, '{}', 'stopped', $3::jsonb)
            """,
            run2,
            tenant_id,
            json.dumps(
                {
                    "n_codes": 3,
                    "conformal": {
                        "set_size_distribution": {"1": 40, "2": 6, "3": 2},
                        "empirical_coverage": 0.93,
                        "nominal_coverage": 0.9,
                    },
                    "exhaustiveness": {"pooled": {"R": 0.55, "tau_res": 0.9}},
                }
            ),
        )

    return {
        "code_a": code_a,
        "code_b": code_b,
        "unit_ids": unit_ids,
        "text_shas": text_shas,
        "gold_a": [CORPUS.theme_units("A")[i]["unit_seq"] for i in (0, 1, N_A, N_A + 1)],
        "gold_b": gold_b_ids,
        "c4": c4_ids,
        "h": h_ids,
        "shared_assignment_id": shared,
        "run2": run2,
    }


@pytest.fixture()
async def seeded_db(monkeypatch):
    from tests import research_db

    # Capture the session-scoped world BEFORE patching: the session
    # research_dsn fixture binds the eventsourcing singleton to the session
    # DB; reset_singletons() below rebinds it to THIS throwaway DB, and the
    # teardown must restore the binding or every later get_app() user in the
    # session talks to a dropped database (full-suite ordering poison).
    original_db_name = research_db.DB_NAME
    original_dsn = research_db.DSN
    saved_url = os.environ.get("CORPUS_KB_DATABASE_URL")
    saved_snapshot = os.environ.get("CORPUS_KB_SNAPSHOT_PERIOD")

    monkeypatch.setattr(research_db, "DB_NAME", "corpus_kb_t17_test")
    monkeypatch.setattr(research_db, "DSN", f"{research_db.BASE_DSN}/corpus_kb_t17_test")
    dsn = await research_db.create_research_db()
    research_db.set_env(dsn)
    await research_db.reset_singletons()
    import asyncpg

    pool = await asyncpg.create_pool(dsn)
    try:
        seeds = await _seed(pool, research_db.TEST_TENANT)
        yield pool, research_db.TEST_TENANT, seeds
    finally:
        await pool.close()
        await research_db.drop_research_db()
        if await _db_exists(original_db_name):
            # Re-bind the eventsourcing singleton to the session DB exactly
            # the way the session fixture left it (env -> reset -> construct).
            os.environ["CORPUS_KB_DATABASE_URL"] = original_dsn
            await research_db.reset_singletons()
            from corpus_kb.domain.application import get_app

            get_app()
        if saved_url is None:
            os.environ.pop("CORPUS_KB_DATABASE_URL", None)
        else:
            os.environ["CORPUS_KB_DATABASE_URL"] = saved_url
        if saved_snapshot is None:
            os.environ.pop("CORPUS_KB_SNAPSHOT_PERIOD", None)
        else:
            os.environ["CORPUS_KB_SNAPSHOT_PERIOD"] = saved_snapshot


async def _db_exists(db_name: str) -> bool:
    import asyncpg

    from tests import research_db

    admin = await asyncpg.connect(research_db.SUPER_DSN, user="postgres", password="postgres")
    try:
        return bool(await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name))
    finally:
        await admin.close()


async def _mint_v1(pool: Any, tenant_id: str, seeds: dict[str, Any]) -> str:
    """Codebook v1 via the REAL aggregate: codes A + B with gold exemplar refs."""
    from corpus_kb.handlers.research_handler import ResearchHandler

    handler = ResearchHandler(pool)
    gold_shas = {
        "a": [seeds["text_shas"][u] for u in seeds["gold_a"]],
        "b": [seeds["text_shas"][u] for u in seeds["gold_b"]],
    }
    codes = [
        {
            "code_id": seeds["code_a"],
            "name": "Billing problems",
            "definition": "Units describing billing, invoice, or payment workflow problems.",
            "inclusion": "billing, invoice",
            "exclusion": "",
            "mode": "deductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        },
        {
            "code_id": seeds["code_b"],
            "name": "Scheduling conflicts",
            "definition": "Units describing technician scheduling or calendar conflicts.",
            "inclusion": "scheduling",
            "exclusion": "",
            "mode": "deductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        },
    ]
    result = handler.create_codebook_version(
        UUID(tenant_id), "v1-billing-scheduling", codes, notes="todo-17 fixture v1"
    )
    from corpus_kb.domain.application import get_app
    from corpus_kb.domain.codebook import CodebookVersion

    version = get_app().repository.get(uuid_mod.UUID(str(result["version_id"])))
    assert isinstance(version, CodebookVersion)
    for code, key in ((codes[0], "a"), (codes[1], "b")):
        version.update_prototypes(
            tenant_id=UUID(tenant_id),
            code_id=UUID(str(code["code_id"])),
            exemplar_text_sha256=gold_shas[key],
        )
        handler.app.save(version)
    return str(result["version_id"])


def _g3_block() -> dict[str, Any]:
    """Offline G3 audit over synthetic audit rows (todo-16 harness shape)."""
    from dataclasses import asdict

    from corpus_kb.research.g3_audit import AuditRow, run_g3_audit

    rng = np.random.default_rng(5)
    rows = [
        AuditRow(
            code_id="a" if i % 2 else "b",
            signal_values={"margin": float(rng.random())},
            override=1 if rng.random() < 0.25 else 0,
            llm_label=1,
            human_label=1 if rng.random() < 0.85 else 0,
            confidence=float(rng.random()),
        )
        for i in range(30)
    ]
    report = run_g3_audit(rows, ["margin"], human_alpha_ceiling=0.85)
    return dict(asdict(report))


async def _explain_proofs(pool: Any, tenant_id: str) -> dict[str, str]:
    from corpus_kb.research.analytics_sql import (
        HALFVEC_PROBE_SQL,
        RETRIEVAL_EXCHANGES_SQL,
        RETRIEVAL_UNITS_SQL,
        explain_plan,
    )
    from corpus_kb.storage.tenant_conn import tenant_connection

    probe = _sql_vec(_embedding_256(_vec(9)))
    halfvec_probe = _sql_vec(_vec(9))
    async with tenant_connection(pool, tenant_id) as conn:
        units = await explain_plan(conn, RETRIEVAL_UNITS_SQL, [probe, 5])
        exchanges = await explain_plan(conn, RETRIEVAL_EXCHANGES_SQL, [probe, 5])
        halfvec = await explain_plan(conn, HALFVEC_PROBE_SQL, [halfvec_probe, 5])
    return {
        "residual_units": units,
        "residual_exchanges": exchanges,
        "retrieval_halfvec": halfvec,
    }


async def test_report_fixture_end_to_end(seeded_db, tmp_path, monkeypatch):
    from corpus_kb.domain.application import get_app
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.research.governance_report import to_dict, validate_required
    from corpus_kb.research.keyword_surface import run_keyword_synthesis
    from corpus_kb.research.promote_code import promote_proposal
    from corpus_kb.research.report_runner import build_report

    pool, tenant_id, seeds = seeded_db
    projection = ResearchProjection(
        pool, CheckpointManager(pool), DLQHandler(pool), ResearchEmbedder(pool, _StubEmbedder())
    )
    reader = EventReader(pool, get_app().mapper, get_app().recorder.events_table_name)

    version_id = await _mint_v1(pool, tenant_id, seeds)
    await projection.catch_up(reader)

    # v1 -> v2 promotion of the c4 proposal through the REAL human gate.
    proposed_id = await _first_proposed_id(pool, tenant_id)
    promotion = await promote_proposal(
        pool,
        UUID(tenant_id),
        proposed_id,
        "Training feedback",
        "Units describing onboarding training feedback.",
        label="training",
    )
    assert promotion["status"] == "promoted", promotion
    v2_id = str(promotion["codebook_version_id"])
    code_c = str(promotion["code_id"])
    await projection.catch_up(reader)

    # Consolidated working version (v3): A + B + C via the real handler
    # surface — one aggregate owns all three codes so the synthesis emits
    # KeywordSetUpdated events on their owning version.
    version_id = await _mint_working_v3(pool, tenant_id, seeds, code_c)
    await projection.catch_up(reader)

    # Give code C five supporting units -> the provisional floor proof (5 < 15).
    await _assign_after_promotion(pool, tenant_id, version_id, code_c, seeds)

    # Keyword synthesis on the working version (A, B, C) -> events + hits.
    synthesis = await run_keyword_synthesis(pool, UUID(tenant_id), UUID(version_id))
    assert synthesis["status"] == "success", synthesis
    await projection.catch_up(reader)

    report = await build_report(
        pool, UUID(tenant_id), UUID(version_id), level="expert", g3_report=_g3_block()
    )
    payload = to_dict(report)

    # 1. SCHEMA PIN: every required field present; not a stub.
    assert validate_required(payload) == []

    # 2. tau_res CALIBRATED and DIFFERS per source type on the two-type fixture.
    residual = payload["residual"]
    by_type = dict(residual["by_source_type"])
    assert set(by_type) == {"interview", "meeting"}
    tau_i = float(dict(by_type["interview"])["tau_res"])
    tau_m = float(dict(by_type["meeting"])["tau_res"])
    assert tau_i != tau_m
    assert tau_i > 0.9  # tight interview gold

    # 3. HIDDEN THEME DETECTED via residual/low-r surfacing + review queue.
    candidates = payload["missing_codes"]["candidates"]
    candidate_units = {u for c in candidates for u in c["unit_ids"]}
    hidden_unit_ids = {seeds["unit_ids"][u] for u in seeds["h"]}
    assert hidden_unit_ids <= candidate_units
    assert payload["missing_codes"]["surfaced_to_review_queue"] >= len(seeds["h"])

    # 4. Multi-category membership visible (the shared unit holds A and B).
    assert payload["review_backlog"]["multi_category_units"] >= 1

    # 5. Semantic-overlap flag on the seeded collision pair.
    overlap = payload["overlap"]
    flagged = {(f["a"], f["b"]) for f in overlap["flagged_pairs"]}
    pair = {seeds["code_a"], seeds["code_b"]}
    assert any(set(p) == pair for p in flagged)
    assert overlap["per_code_silhouette"]

    # 6. Keyword-conflict flags + hit_location populations + provisional floor.
    keywords = payload["keywords"]
    assert keywords["conflicts"]
    locations = set()
    for code in dict(keywords["per_code"]).values():
        for kind_populations in dict(dict(code)["hit_locations"]).values():
            locations |= set(dict(kind_populations))
    assert locations & {"answer", "question", "both"}
    code_c_section = dict(dict(keywords["per_code"])[code_c])
    assert code_c_section["support_n"] == 5
    assert any(kw["provisional"] for kw in code_c_section["positive"])

    # 7. Dual IRR: alpha + kappa + AC1 per code, bands, no false halt.
    irr = payload["kappa_alpha"]
    per_code = {v["code_id"]: v for v in irr["per_code"]}
    assert seeds["code_a"] in per_code and seeds["code_b"] in per_code
    assert all("ac1" in v and "band" in v for v in per_code.values())

    # 8. Coverage curve: seeded prior R values + live R -> negative slope.
    curve = payload["coverage_curve"]
    assert float(curve["R_slope"]) < 0
    assert curve["plateau_detected"] is False
    assert curve["deductive_coverage_excluded"] is True

    # 9. Stability + DBCV + conformal + G3 blocks present with numerics.
    stability = payload["cluster_stability"]
    assert stability["n_resamples"] == 10
    assert stability["mean_pairwise_ari"] is not None
    assert "dbcv_relative_validity" in stability
    conformal = payload["conformal"]
    assert conformal["set_size_distribution"] == {"1": 40, "2": 6, "3": 2}
    g3 = payload["g3_audit"]
    assert "combined_metrics" in g3 and "human_parity" in g3

    # 10. ISR per source type + gray-zone share + link accuracy.
    assert set(payload["isr_by_source_type"]) == {"interview", "meeting"}
    assert payload["gray_zone_share"] > 0
    assert payload["link_accuracy"]["n_exchanges"] == len(CORPUS.rows)

    # 11. Codebook diff across the version chain (working set diffed vs prior).
    diff = payload["codebook_diff"]
    assert diff["codes_added"]
    codebook_definitions = set(dict(payload["keywords"]["per_code"]))
    assert codebook_definitions == {seeds["code_a"], seeds["code_b"], code_c}

    # 12. EXPLAIN: HNSW indexes used (analytics 256-d + retrieval halfvec);
    # no seq-scan on the vector tables.
    explains = await _explain_proofs(pool, tenant_id)
    assert "idx_research_units_hnsw_256" in explains["residual_units"]
    assert "Seq Scan" not in explains["residual_units"]
    assert "idx_research_exchanges_hnsw_256" in explains["residual_exchanges"]
    assert "Seq Scan" not in explains["residual_exchanges"]
    assert "idx_research_units_hnsw_halfvec" in explains["retrieval_halfvec"]
    assert "Seq Scan" not in explains["retrieval_halfvec"]

    # 13. Novice level: traffic lights + next actions over the ONE computation.
    novice = await build_report(pool, UUID(tenant_id), UUID(v2_id), level="novice")
    novice_view = dict(to_dict(novice)["novice_view"])
    assert novice_view["traffic_lights"] and novice_view["next_actions"]

    # Evidence artifact (task-17 JSON companion) via env seam.
    evidence_path = os.environ.get("CORPUS_KB_REPORT_EVIDENCE")
    if evidence_path:
        Path(evidence_path).write_text(
            json.dumps(
                {
                    "report": payload,
                    "explains": explains,
                    "promotion": promotion,
                    "synthesis_conflicts": synthesis["conflicts"],
                    "code_c": code_c,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )


class _StubEmbedder:
    model = "stub"
    model_revision = "1024"


async def _mint_working_v3(pool: Any, tenant_id: str, seeds: dict[str, Any], code_c: str) -> str:
    """Consolidated working version: A + B + C via the REAL handler surface."""
    from corpus_kb.domain.application import get_app
    from corpus_kb.domain.codebook import CodebookVersion
    from corpus_kb.handlers.research_handler import ResearchHandler

    handler = ResearchHandler(pool)
    gold_shas = {
        seeds["code_a"]: [seeds["text_shas"][u] for u in seeds["gold_a"]],
        seeds["code_b"]: [seeds["text_shas"][u] for u in seeds["gold_b"]],
        code_c: [seeds["text_shas"][u] for u in seeds["c4"][:5]],
    }
    codes = [
        {
            "code_id": seeds["code_a"],
            "name": "Billing problems",
            "definition": "Units describing billing, invoice, or payment workflow problems.",
            "inclusion": "billing, invoice",
            "exclusion": "",
            "mode": "deductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        },
        {
            "code_id": seeds["code_b"],
            "name": "Scheduling conflicts",
            "definition": "Units describing technician scheduling or calendar conflicts.",
            "inclusion": "scheduling",
            "exclusion": "",
            "mode": "deductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        },
        {
            "code_id": code_c,
            "name": "Training feedback",
            "definition": "Units describing onboarding training feedback.",
            "inclusion": "training",
            "exclusion": "",
            "mode": "inductive",
            "is_interpretive": False,
            "allows_question_dependent": True,
        },
    ]
    result = handler.create_codebook_version(
        UUID(tenant_id), "v3-working", codes, notes="todo-17 fixture working set"
    )
    version = get_app().repository.get(uuid_mod.UUID(str(result["version_id"])))
    assert isinstance(version, CodebookVersion)
    for code in codes:
        refs = gold_shas.get(str(code["code_id"]), [])
        if refs:
            version.update_prototypes(
                tenant_id=UUID(tenant_id),
                code_id=UUID(str(code["code_id"])),
                exemplar_text_sha256=refs,
            )
            handler.app.save(version)
    return str(result["version_id"])


async def _first_proposed_id(pool: Any, tenant_id: str) -> int:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            """
            SELECT proposed_id FROM research_proposed_codes
            WHERE tenant_id = $1 AND status = 'proposed'
            ORDER BY n_members DESC LIMIT 1
            """,
            tenant_id,
        )
    assert value is not None
    return int(value)


async def _assign_after_promotion(
    pool: Any, tenant_id: str, version_id: str, code_c: str, seeds: dict[str, Any]
) -> None:
    from corpus_kb.storage.tenant_conn import tenant_connection

    c4_units = [seeds["unit_ids"][u] for u in seeds["c4"][:5]]
    async with tenant_connection(pool, tenant_id) as conn:
        # The seeded v1-era rows are re-examined by the v2 report (re-run).
        await conn.execute(
            "UPDATE research_assignments SET cb_version_id = $2 "
            "WHERE tenant_id = $1 AND cb_version_id IS NULL",
            tenant_id,
            version_id,
        )
        for unit_id in c4_units:
            await conn.execute(
                """
                INSERT INTO research_assignments
                (tenant_id, assignment_aggregate_id, unit_id, code_id,
                 cb_version_id, sim_answer, sim_qa, sim_q, evidence_basis,
                 stance, term_origin, rationale, confidence, tier_fired, status)
                VALUES ($1, $2, $3, $4, $5, 0.97, 0.97, 0.5,
                        'explicit_in_answer', 'affirm', 'participant',
                        'deductive:v2:explicit', 'high', 0, 'auto')
                """,
                tenant_id,
                str(uuid_mod.uuid4()),
                unit_id,
                code_c,
                version_id,
            )

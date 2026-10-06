"""Wire keyword_governance + keyness into the pooling pass.

keyword_governance.py and keyness.py are pure functions over counts a caller
supplies; this module is the DB-facing wiring that assembles those counts from
the materialized chunk_keyword_hits/code_keywords state and reports what it
finds. Kept out of pooling.py (that file's functions each write one signal
table; this one is read-only and reports across all codes at once) following
the same "extract to a sibling module" split as codebook_loader.py.

Called from CodingHandler.handle_pool, after materialize_chunk_keyword_hits
has run for this pooling pass, so the hit counts here reflect the current
codebook.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.coding.keyness import g2, log_odds_dirichlet
from corpus_kb.coding.keyword_governance import criterial_owner, find_collisions, over_df_cap
from corpus_kb.storage.tenant_conn import tenant_connection

_DF_CAP_FRACTION = 0.012


async def compute_keyword_governance(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    cap_frac: float = _DF_CAP_FRACTION,
) -> dict[str, Any]:
    """Report keyword collisions, over-cap keywords, and keyness for one tenant.

    - collisions: active code_keywords whose exact (lowercased) text is claimed
      by more than one code_id, across BOTH inclusion and exclusion kinds. The
      DB's own unique index (migration 011) already blocks two ACTIVE
      INCLUSION rows for the same lower(keyword), but an inclusion keyword for
      one code and an exclusion keyword with the same text for another code
      are not blocked and are exactly the ambiguous case this flags.
    - resolutions: for each collision term, the single code whose
      inclusion_criteria literally contains it (criterial_owner), or null when
      zero or more than one code's criteria matches.
    - over_cap: (code_id, keyword) pairs whose hit-chunk ratio exceeds
      `cap_frac` of the corpus -- a keyword so common it stops discriminating.
    - keyness: per (code_id, keyword) G2/log-ratio and Monroe log-odds z-score,
      contrasting this code's use of the term against every other code's
      combined keyword-hit volume. Also persisted onto code_keywords.ll /
      .log_ratio (both NULL until now) so the score survives past this response.
    """
    async with tenant_connection(pool, tenant_id) as conn:
        claim_rows = await conn.fetch(
            "SELECT keyword, code_id FROM code_keywords WHERE tenant_id=$1 AND status='active'",
            tenant_id,
        )
        hit_rows = await conn.fetch(
            """
            SELECT code_id, keyword, SUM(n_hits) AS hits, COUNT(DISTINCT chunk_id) AS hit_chunks
            FROM chunk_keyword_hits
            WHERE tenant_id=$1
            GROUP BY code_id, keyword
            """,
            tenant_id,
        )
        corpus_chunks = await conn.fetchval(
            "SELECT COUNT(*) FROM chunks WHERE tenant_id=$1", tenant_id
        )
        criteria_rows = await conn.fetch(
            "SELECT code_id, inclusion_criteria FROM code_registry "
            "WHERE tenant_id=$1 AND codebook_version_id=$2",
            tenant_id,
            codebook_version_id,
        )

        claims: dict[str, list[str]] = {}
        for row in claim_rows:
            key = row["keyword"].lower()
            codes = claims.setdefault(key, [])
            if row["code_id"] not in codes:
                codes.append(row["code_id"])
        collisions = find_collisions(claims)

        code_criteria = {r["code_id"]: r["inclusion_criteria"] or "" for r in criteria_rows}
        resolutions = {term: criterial_owner(term, code_criteria) for term in collisions}

        hits_by_pair: dict[tuple[str, str], tuple[int, int]] = {
            (r["code_id"], r["keyword"]): (int(r["hits"] or 0), int(r["hit_chunks"] or 0))
            for r in hit_rows
        }
        total_by_code: dict[str, int] = {}
        for (code_id, _keyword), (hits, _chunks) in hits_by_pair.items():
            total_by_code[code_id] = total_by_code.get(code_id, 0) + hits
        total_hits_overall = sum(total_by_code.values())

        over_cap: list[dict[str, Any]] = []
        keyness: list[dict[str, Any]] = []
        for (code_id, keyword), (hits, hit_chunks) in sorted(hits_by_pair.items()):
            if corpus_chunks and over_df_cap(hit_chunks, corpus_chunks, cap_frac=cap_frac):
                over_cap.append(
                    {
                        "code_id": code_id,
                        "keyword": keyword,
                        "hit_chunks": hit_chunks,
                        "corpus_chunks": corpus_chunks,
                    }
                )

            target_total = total_by_code.get(code_id, 0)
            ref_total = total_hits_overall - target_total
            # b: this same keyword's hits under every OTHER code.
            b = sum(
                h for (cid, kw), (h, _c) in hits_by_pair.items() if kw == keyword and cid != code_id
            )
            ll, log_ratio = g2(a=hits, b=b, target_total=target_total, ref_total=ref_total)
            z = log_odds_dirichlet(a=hits, b=b, target_total=target_total, ref_total=ref_total)
            keyness.append(
                {
                    "code_id": code_id,
                    "keyword": keyword,
                    "g2": ll,
                    "log_ratio": log_ratio,
                    "log_odds_z": z,
                }
            )
            await conn.execute(
                "UPDATE code_keywords SET ll=$1, log_ratio=$2 "
                "WHERE tenant_id=$3 AND code_id=$4 AND keyword=$5 AND status='active'",
                ll,
                log_ratio,
                tenant_id,
                code_id,
                keyword,
            )

    return {
        "collisions": collisions,
        "resolutions": resolutions,
        "over_cap": over_cap,
        "keyness": keyness,
    }

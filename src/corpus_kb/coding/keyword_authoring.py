"""Authored keyword rows: one codebook load's writes and conflict refusals.

The loader (codebook_loader.py) stays inside the 250-line gate, so the
keyword-writing half of phase 3 lives here (TD-10). This module also
holds the sentence-level include/exclude tag rule. Authored rows carry
``source = 'codebook'`` and coexist with synthesized rows, separated by
the existing ``code_keywords.source`` column (athena G-12). The sentence
regex follows ``research/notebook.py``; this module owns its copy so
coding does not import the ask domain.
"""

from __future__ import annotations

import re
from typing import Any

from corpus_kb.coding._scope import ScopedConn
from corpus_kb.coding.codebook import CodebookError

__all__ = [
    "exclusion_blocked",
    "keyword_tag_decision",
    "retire_code_keywords",
    "split_sentences",
    "write_codebook_keywords",
]

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


async def retire_code_keywords(conn: ScopedConn, tenant: str) -> int:
    """Retire every active authored keyword row (the reload pin, athena row 47).

    A new codebook version's authored keywords replace the previous
    version's wholesale: the one-active-inclusion-keyword-per-tenant index
    (migration 012) forces delete-then-insert semantics anyway, so the
    loader calls this BEFORE the new version's inserts (refine D-1).
    Synthesized rows keep their own lifecycle (``source`` separates them).
    Answers the number of retired rows.
    """
    status = await conn.execute(
        "UPDATE code_keywords SET status='retired'"
        " WHERE tenant_id=$1 AND source='codebook' AND status='active'",
        tenant,
    )
    return int(str(status).split()[-1])


async def write_codebook_keywords(
    conn: ScopedConn, tenant: str, embedded: list[tuple[dict[str, Any], str, str]]
) -> None:
    """Write authored keywords as active ``codebook``-source rows (TD-10).

    Refuses a duplicate active include term across codes with a typed
    CodebookError naming the term and both codes: the one-active-inclusion-
    keyword-per-tenant index (migration 012) makes the conflict real
    (athena G-11). Exclusion rows carry no such constraint. Idempotent via
    the table's primary key. Called inside the loader's write transaction.
    """
    claims: dict[str, str] = {}
    for code, _dv, _pv in embedded:
        keywords = code.get("keywords")
        if not isinstance(keywords, dict):
            continue
        code_id = str(code["code_id"] if "code_id" in code else code.get("id"))
        for kind, key in (("inclusion", "include"), ("exclusion", "exclude")):
            for term in keywords.get(key) or []:
                if kind == "inclusion":
                    await _refuse_duplicate_include(conn, tenant, str(term), code_id, claims)
                    claims[str(term).lower()] = code_id
                await conn.execute(
                    "INSERT INTO code_keywords (keyword, code_id, tenant_id, kind, source, status)"
                    " VALUES ($1, $2, $3, $4, 'codebook', 'active') ON CONFLICT DO NOTHING",
                    term,
                    code_id,
                    tenant,
                    kind,
                )


async def _refuse_duplicate_include(
    conn: ScopedConn, tenant: str, term: str, code_id: str, claims: dict[str, str]
) -> None:
    """Raise when this active include term is already claimed by another code.

    Two checks: a term this same document already gave another code, and a
    term an earlier load left active under a different code.
    """
    claimed = claims.get(term.lower())
    if claimed is not None and claimed != code_id:
        raise CodebookError(
            f"Include keyword {term!r} is claimed by codes {claimed!r} and {code_id!r}"
        )
    row = await conn.fetchrow(
        "SELECT code_id FROM code_keywords WHERE tenant_id=$1"
        " AND kind='inclusion' AND status='active' AND lower(keyword)=lower($2)",
        tenant,
        term,
    )
    if row is not None and str(row["code_id"]) != code_id:
        raise CodebookError(
            f"Include keyword {term!r} is already active for code "
            f"{str(row['code_id'])!r}; refused for code {code_id!r}"
        )


def split_sentences(text: str) -> list[str]:
    """Split one chunk text into sentences (deterministic, stdlib regex)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern for one keyword term."""
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def _sentences_with_hits(sentences: list[str], terms: list[str]) -> dict[int, set[str]]:
    """Sentence index -> the terms of `terms` that match inside it."""
    if not terms:
        return {}
    patterns = [(term, _term_pattern(term)) for term in terms]
    hits: dict[int, set[str]] = {}
    for index, sentence in enumerate(sentences):
        matched = {term for term, pattern in patterns if pattern.search(sentence)}
        if matched:
            hits[index] = matched
    return hits


def exclusion_blocked(
    text: str,
    include_terms: list[str],
    exclude_terms: list[str],
) -> dict[str, object]:
    """The block rule: ``tag`` only when no exclude term shares a hit sentence.

    Returns ``{"tag": bool, "blocked": bool, "include_hits": [str],
    "block_terms": [str]}``; sorted term lists keep it deterministic.
    """
    sentences = split_sentences(text)
    include_hits = _sentences_with_hits(sentences, include_terms)
    if not include_hits:
        return {"tag": False, "blocked": False, "include_hits": [], "block_terms": []}
    exclude_hits = _sentences_with_hits(sentences, exclude_terms)
    block_terms: set[str] = set()
    for index in include_hits:
        block_terms |= exclude_hits.get(index, set())
    blocked = bool(block_terms)
    return {
        "tag": not blocked,
        "blocked": blocked,
        "include_hits": sorted({t for terms in include_hits.values() for t in terms}),
        "block_terms": sorted(block_terms),
    }


def keyword_tag_decision(
    text: str,
    keywords: dict[str, list[str]] | None,
) -> dict[str, object]:
    """One chunk x code decision from an authored ``keywords`` block (may be None).

    With include terms alone, any hit tags. The answer names the evidence
    so a report can quote it; it never touches stored human codes.
    """
    include = list((keywords or {}).get("include") or [])
    exclude = list((keywords or {}).get("exclude") or [])
    if not include:
        return {
            "tag": False,
            "blocked": False,
            "include_hits": [],
            "block_terms": [],
            "reason": "no include keywords",
        }
    outcome = exclusion_blocked(text, include, exclude)
    if not outcome["include_hits"]:
        outcome["reason"] = "no include keyword hit"
    elif outcome["blocked"]:
        outcome["reason"] = "exclude keyword shares the sentence with an include hit"
    else:
        outcome["reason"] = "include keyword hit, no exclusion"
    return outcome

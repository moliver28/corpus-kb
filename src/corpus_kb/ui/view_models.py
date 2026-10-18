"""Pure view models for the browser UI pages (UX program Wave 3).

Every function here calls the tool layer and returns plain dicts the
NiceGUI pages render. No NiceGUI import anywhere in this module: tests
drive it offline by monkeypatching the tool functions (imported at
module top, so ``corpus_kb.ui.view_models.ask`` and friends are the
seams). One tool call per page flow mirrors the MCP/HTTP surfaces, so
the UI shows the same envelopes (ask status, ingest receipts, demo
narration). The Overview page's flows live in ``ui/health.py``; shared
string shaping lives in ``ui/render.py``.
"""

from __future__ import annotations

from typing import Any, cast

import asyncpg

from corpus_kb.tools.ask_tools import ask
from corpus_kb.tools.demo_tools import research_demo
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID
from corpus_kb.tools.ingest_tools import ingest_file, list_documents
from corpus_kb.tools.ingest_url_tools import ingest_url
from corpus_kb.tools.topics_tools import topics_list

__all__ = [
    "add_via_path",
    "add_via_url",
    "ask_view",
    "demo_receipt",
    "library_rows",
    "verify_badge",
]

# The verify badge's "verified" bar: at or above this groundedness the
# judge entailed every claim it checked. Below it the answer is "partial".
VERIFY_FULL_GROUNDEDNESS = 0.99

_ASK_STATUS_NOTES: dict[str, str] = {
    "empty": "No evidence matched this question. Try other words, or add documents first.",
    "degraded": "Answer generation is unavailable. Showing the source text without an answer.",
}

_DEMO_INCOMPLETE_NOTE = "The demo did not finish. Read the steps below."
_POOL_MISSING_MESSAGE = (
    "The browser UI needs the server's database pool. Start with corpus-kb start."
)


def verify_badge(verify: dict[str, Any] | None) -> dict[str, Any]:
    """Map the ask result's verify record to one honest badge.

    Abstained (no judge ran, or nothing to check) is "not verified" —
    never presented as a pass. A full entailment set is "verified";
    anything else is "partial".
    """
    record: dict[str, Any] = verify or {}
    raw = record.get("groundedness", 0.0)
    groundedness = float(raw) if isinstance(raw, (int, float)) else 0.0
    # No verify record at all is the same honesty as an abstention: the
    # answer was never checked, so the badge never claims a pass.
    if not record or bool(record.get("abstained", False)):
        return {"label": "not verified", "groundedness": groundedness}
    label = "verified" if groundedness >= VERIFY_FULL_GROUNDEDNESS else "partial"
    return {"label": label, "groundedness": groundedness}


async def ask_view(
    query: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """One cited answer, shaped for the Ask page.

    Returns ``{status, sentences, sources, verify_badge, note,
    degraded_excluded}``. Non-success statuses keep their honest note
    (``_ASK_STATUS_NOTES``) and whatever evidence the tool returned.
    """
    payload = await ask(query, config=config, tenant_id=tenant_id, k=k, pg_pool=pg_pool)
    status = str(payload.get("status", "empty"))
    result = payload.get("result")
    result_dict = result if isinstance(result, dict) else {}
    verify = result_dict.get("verify")
    verify_dict = verify if isinstance(verify, dict) else None
    note = str(result_dict.get("note") or "") or _ASK_STATUS_NOTES.get(status, "")
    return {
        "status": status,
        "sentences": _dict_list(result_dict.get("sentences")),
        "sources": _dict_list(result_dict.get("sources")),
        "verify_badge": verify_badge(verify_dict),
        "note": note,
        "degraded_excluded": payload.get("degraded_excluded", {}),
    }


async def library_rows(
    config: dict[str, Any] | None,
    pool: asyncpg.Pool,
    limit: int = 100,
) -> dict[str, Any]:
    """The Library page's rows: documents and discovered topics.

    Both reads route through the same tool functions the MCP/HTTP
    surfaces use, so the page never grows a second read path.
    """
    docs = await list_documents(pool, config, limit=limit)
    topics = await topics_list(pool, config, limit=limit)
    doc_rows = [
        {
            "id": str(doc.get("doc_id", "")),
            "title": str(doc.get("source") or doc.get("title") or "Untitled"),
            "type": str(doc.get("source_type", "")),
            "chunks": int(doc.get("chunk_count", 0) or 0),
        }
        for doc in _dict_list(docs.get("result"))
    ]
    topic_rows = [
        {
            "id": str(topic.get("topic_id", "")),
            "label": str(topic.get("reduced_label") or topic.get("label") or "topic"),
            "size": int(topic.get("size", 0) or 0),
        }
        for topic in _dict_list(topics.get("topics"))
    ]
    return {"documents": doc_rows, "topics": topic_rows}


async def add_via_path(
    path: str,
    config: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    source_type: str | None = None,
) -> dict[str, Any]:
    """Ingest one file through the standard tool; shape the receipt."""
    result = await ingest_file(str(path), _pool_or_raise(pool), config, tenant_id, source_type)
    return _receipt(result)


async def add_via_url(
    url: str,
    config: dict[str, Any] | None = None,
    pool: asyncpg.Pool | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Ingest one website through the standard tool; shape the receipt."""
    result = await ingest_url(str(url), _pool_or_raise(pool), config, tenant_id)
    ok = str(result.get("status", "error")) == "success"
    pages = _dict_list(result.get("pages"))
    failed = sum(1 for page in pages if str(page.get("status", "")) not in {"success", "skipped"})
    return {
        "ok": ok,
        "status": str(result.get("status", "error")),
        "pages": len(pages),
        "documents": int(cast("int", result.get("total_documents", 0))),
        "failed_pages": failed,
        "message": "" if ok else str(result.get("message", "")),
    }


async def demo_receipt(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The narrated demo, honestly: narration, status, never a crash.

    A missing demo corpus (non-editable installs) surfaces as the tool's
    own narration or this module's error row — the page stays up either way.
    """
    try:
        result = await research_demo(config)
    except Exception as exc:  # the page must never crash on the demo
        return {
            "ok": False,
            "status": "failed",
            "narration": "",
            "message": f"The demo could not start: {exc}",
        }
    status = str(result.get("status", "failed"))
    return {
        "ok": status == "completed",
        "status": status,
        "narration": str(result.get("narration", "")),
        "message": "" if status == "completed" else _DEMO_INCOMPLETE_NOTE,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _dict_list(value: object) -> list[dict[str, Any]]:
    """A JSON list of objects, or an empty list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pool_or_raise(pool: asyncpg.Pool | None) -> asyncpg.Pool:
    """The UI pages run inside the wired server, so the pool always exists."""
    if pool is None:
        raise ValueError(_POOL_MISSING_MESSAGE)
    return pool


def _receipt(result: dict[str, Any]) -> dict[str, Any]:
    """One ingest receipt: ok flag, counts, and the error text + fix."""
    status = str(result.get("status", "error"))
    ok = status == "success"
    return {
        "ok": ok,
        "status": status,
        "message": "" if ok else str(result.get("message", "")),
        "remediation": str(result.get("remediation", "")),
        "document_id": str(result.get("document_id", "") or ""),
        "chunks": int(result.get("chunk_count", 0) or 0),
        "entities": int(result.get("entity_count", 0) or 0),
    }

"""The Add page's sample-knowledge view model (UX program Wave 6).

Pure like ``view_models.py``: calls the tool layer and returns plain
dicts the NiceGUI page renders. No NiceGUI import here — tests drive it
by monkeypatching the ``load_demo_kb`` seam (imported at module top, so
``demo_kb_vm.load_demo_kb`` is the seam). One tool call mirrors the CLI
(``corpus-kb demo-kb``), so the page shows the same receipt envelope.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.demo_kb import DemoCorpusMissingError, load_demo_kb

__all__ = ["demo_kb_view"]

_POOL_MISSING_MESSAGE = (
    "The browser UI needs the server's database pool. Start with corpus-kb start."
)


def _empty_receipt() -> dict[str, object]:
    """The failure-shaped receipt every early return starts from."""
    return {
        "ok": False,
        "status": "error",
        "ingested": [],
        "skipped": [],
        "chunks": 0,
        "message": "",
        "remediation": "",
    }


async def demo_kb_view(
    config: dict[str, object] | None,
    pool: asyncpg.Pool | None,
) -> dict[str, object]:
    """The sample-knowledge receipt, shaped for the Add page.

    Calls the demo corpus loader through the same ``load_demo_kb`` seam
    the CLI uses. A missing corpus (installed wheels) surfaces as the
    typed error's remediation — the page stays up either way.
    """
    if pool is None:
        return {**_empty_receipt(), "message": _POOL_MISSING_MESSAGE}
    try:
        envelope = await load_demo_kb(config, pool)
    except DemoCorpusMissingError as exc:
        return {
            **_empty_receipt(),
            "status": "missing",
            "message": str(exc),
            "remediation": exc.remediation,
        }
    except Exception as exc:  # the page must never crash on the loader
        return {
            **_empty_receipt(),
            "status": "failed",
            "message": f"The sample corpus failed: {exc}",
        }
    result = envelope.get("result")
    result_dict = result if isinstance(result, dict) else {}
    status = str(envelope.get("status", "error"))
    return {
        "ok": status == "success",
        "status": status,
        "ingested": [str(name) for name in _str_list(result_dict.get("ingested"))],
        "skipped": [str(name) for name in _str_list(result_dict.get("skipped"))],
        "chunks": _total_chunks(result_dict.get("chunk_counts")),
        "message": str(envelope.get("message", "")),
        "remediation": "",
    }


def _str_list(value: object) -> list[object]:
    """A JSON list of anything, or an empty list."""
    if not isinstance(value, list):
        return []
    return list(value)


def _total_chunks(chunk_counts: object) -> int:
    """The summed chunk count map, or 0."""
    if not isinstance(chunk_counts, dict):
        return 0
    return sum(int(count) for count in chunk_counts.values() if isinstance(count, (int, float)))

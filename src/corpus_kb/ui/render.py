"""Pure string shaping for the browser UI pages (UX program Wave 3).

The pages render these strings directly; tests assert them exactly. No
NiceGUI import, no tool calls — functions in, markdown out.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "demo_kb_markdown",
    "library_markdown",
    "receipt_line",
    "sources_markdown",
    "url_receipt_line",
]


def demo_kb_markdown(view: dict[str, Any]) -> str:
    """The sample-knowledge receipt as one honest markdown block."""
    if bool(view.get("ok")):
        names = _names(view.get("ingested"))
        return f"**Sample knowledge loaded**: {view.get('chunks', 0)} chunks ({names})."
    message = str(view.get("message", "")) or "The sample corpus could not load."
    remediation = str(view.get("remediation", "")) or ""
    skipped = _names(view.get("skipped"))
    line = f"**Sample knowledge**: {message}"
    if skipped:
        line += f" Unchanged files: {', '.join(skipped)}."
    return line + (f" Fix: {remediation}" if remediation else "")


def _names(value: object) -> list[str]:
    """A JSON list of file names, or an empty list."""
    if not isinstance(value, list):
        return []
    return [str(name) for name in value]


def receipt_line(name: str, receipt: dict[str, Any]) -> str:
    """One Add receipt as one markdown line (honest on failure)."""
    if bool(receipt.get("ok")):
        return (
            f"**{name}** added: {receipt.get('chunks', 0)} chunks, "
            f"{receipt.get('entities', 0)} connections found."
        )
    message = str(receipt.get("message", "")) or "The file could not be added."
    remediation = str(receipt.get("remediation", "")) or ""
    return f"**{name}** failed: {message}" + (f" Fix: {remediation}" if remediation else "")


def url_receipt_line(address: str, receipt: dict[str, Any]) -> str:
    """One URL ingest receipt as one markdown line (honest on failure)."""
    if bool(receipt.get("ok")):
        failed = int(receipt.get("failed_pages", 0) or 0)
        line = (
            f"**{address}** added: {receipt.get('documents', 0)} documents from "
            f"{receipt.get('pages', 0)} pages."
        )
        if failed:
            line += f" {failed} page(s) failed; the rest are in."
        return line
    message = str(receipt.get("message", "")) or "The page could not be added."
    return f"**{address}** failed: {message}"


def sources_markdown(sources: list[dict[str, Any]]) -> str:
    """The Ask page's numbered source list."""
    if not sources:
        return ""
    lines = ["**Sources**"]
    for index, source in enumerate(sources, start=1):
        title = str(source.get("source") or source.get("doc_id") or "document")
        lines.append(f"{index}. {title}")
    return "\n".join(lines)


def library_markdown(documents: list[dict[str, Any]], topics: list[dict[str, Any]]) -> str:
    """The Library page as two small markdown tables (or the honest empty note)."""
    lines: list[str] = []
    if documents:
        lines.append("| Document | Type | Chunks |")
        lines.append("| --- | --- | --- |")
        for doc in documents:
            lines.append(
                f"| {doc.get('title', '')} | {doc.get('type', '')} | {doc.get('chunks', 0)} |"
            )
    else:
        lines.append("No documents yet. Open **Add** to ingest your first file.")
    lines.append("")
    if topics:
        lines.append("| Topic | Documents |")
        lines.append("| --- | --- |")
        for topic in topics:
            lines.append(f"| {topic.get('label', '')} | {topic.get('size', 0)} |")
    return "\n".join(lines)

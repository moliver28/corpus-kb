"""The ``transcript_meta`` validator: research-transcript metadata sanity.

Research transcripts carry their units as chunk metadata (``speaker``,
``seq`` — see ``pipeline.research_units``). This validator checks that
metadata before any write happens:

* every unit names a speaker (non-empty after strip);
* ``seq`` values are ints and strictly increase in document order;
* empty units (no text) are counted — reported as a warning, not failed.

The checks ride the advisory channel: findings are data, the mode never
raises. Documents without transcript metadata pass untouched.
"""

from __future__ import annotations

from .document import IngestDocument
from .validate import Finding


def transcript_meta_findings(doc: IngestDocument) -> list[Finding]:
    """Metadata sanity for research units: speaker, sequence, empty units."""
    findings: list[Finding] = []
    previous_seq: int | None = None
    empty = 0
    for chunk in doc.chunks:
        meta = chunk.metadata
        chunk_id = str(chunk.chunk_id)
        if not chunk.text.strip():
            empty += 1
        speaker = meta.get("speaker")
        if "speaker" in meta and (not isinstance(speaker, str) or not speaker.strip()):
            findings.append(
                Finding(
                    validator="transcript_meta",
                    code="missing-speaker",
                    severity="error",
                    detail=f"chunk {chunk_id} carries no speaker",
                    chunk_id=chunk_id,
                )
            )
        seq = meta.get("seq")
        if seq is None:
            continue
        if not isinstance(seq, int) or isinstance(seq, bool):
            findings.append(
                Finding(
                    validator="transcript_meta",
                    code="seq-not-int",
                    severity="error",
                    detail=f"chunk {chunk_id} seq is not an int",
                    chunk_id=chunk_id,
                )
            )
            continue
        if previous_seq is not None and seq <= previous_seq:
            findings.append(
                Finding(
                    validator="transcript_meta",
                    code="seq-not-increasing",
                    severity="error",
                    detail=f"chunk {chunk_id} seq {seq} does not follow seq {previous_seq}",
                    chunk_id=chunk_id,
                )
            )
        previous_seq = seq
    if empty:
        findings.append(
            Finding(
                validator="transcript_meta",
                code="empty-unit",
                severity="warning",
                detail=f"{empty} chunks hold no text",
            )
        )
    findings.sort(key=lambda f: (f.code, f.chunk_id or "", f.detail))
    return findings

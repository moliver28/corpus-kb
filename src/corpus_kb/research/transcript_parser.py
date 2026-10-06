"""Speaker-turn recovery from transcript files (todo-11 (d), v5 §2).

txt first-class ("Speaker: text" lines); vtt/srt (timestamp blocks, optional
speaker cue), csv (speaker,text columns — header-sniffed), docx via the
existing unstructured partitioner with txt-style line recovery afterwards.
Unknown formats fall back to txt line parsing and are flagged
parse_quality=unverified.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from corpus_kb.domain.transcript import TurnSpec

SPEAKER_LINE = re.compile(r"^\s*([A-Z0-9][\w .'\-]{0,60}?)\s*:\s*(.+)$")
VTT_CUE_TIME = re.compile(r"^\s*(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s*-->\s*")
VTT_SPEAKER = re.compile(r"<v\s+([^>]+)>")
SRT_BLOCK_ID = re.compile(r"^\d+$")

DOCX_PARSE_QUALITY = "verified"
TXT_PARSE_QUALITY = "verified"
FALLBACK_PARSE_QUALITY = "unverified"


@dataclass
class ParsedTranscript:
    source_type: str
    turns: list[TurnSpec] = field(default_factory=list)
    parse_quality: str = TXT_PARSE_QUALITY
    format_name: str = "txt"


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return "txt"
    if suffix in (".vtt",):
        return "vtt"
    if suffix in (".srt",):
        return "srt"
    if suffix in (".csv",):
        return "csv"
    if suffix in (".docx",):
        return "docx"
    return "txt"


def infer_source_type(path: Path, turns: list[TurnSpec]) -> str:
    """v5 source-type inference: moderator+participant dialogue => interview."""
    roles = {t.role for t in turns}
    if {"moderator", "participant"} <= roles:
        return "interview"
    if any(t.role == "moderator" for t in turns) and len({t.speaker for t in turns}) > 2:
        return "meeting"
    return "transcript"


def parse_transcript(path: Path) -> ParsedTranscript:
    """Recover speaker turns from a transcript file (any supported format)."""
    fmt = detect_format(path)
    if fmt == "docx":
        lines = _docx_lines(path)
        turns = _parse_labelled(lines)
        return ParsedTranscript("docx", turns, DOCX_PARSE_QUALITY, fmt)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if fmt == "vtt":
        return ParsedTranscript("vtt", _parse_vtt(raw), "verified", fmt)
    if fmt == "srt":
        return ParsedTranscript("srt", _parse_srt(raw), "verified", fmt)
    if fmt == "csv":
        parsed = _parse_csv(raw)
        if parsed is not None:
            return ParsedTranscript("csv", parsed, "verified", fmt)
        return ParsedTranscript(
            "csv", _parse_labelled(raw.splitlines()), FALLBACK_PARSE_QUALITY, fmt
        )
    turns = _parse_labelled(raw.splitlines())
    quality = TXT_PARSE_QUALITY if turns else FALLBACK_PARSE_QUALITY
    return ParsedTranscript("txt", turns, quality, fmt)


def _parse_labelled(lines: list[str]) -> list[TurnSpec]:
    """'Speaker: text' recovery; unlabelled prose merges into the last turn."""
    turns: list[TurnSpec] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = SPEAKER_LINE.match(stripped)
        if match:
            turns.append(
                TurnSpec(
                    seq=len(turns), speaker=match.group(1).strip(), text=match.group(2).strip()
                )
            )
        elif turns:
            turns[-1].text += " " + stripped
    return turns


def _parse_vtt(raw: str) -> list[TurnSpec]:
    turns: list[TurnSpec] = []
    speaker = "unknown"
    times: tuple[float | None, float | None] = (None, None)
    for line in raw.splitlines():
        stripped = line.strip()
        if VTT_CUE_TIME.match(stripped):
            times = _cue_times(stripped)
            continue
        if not stripped or stripped in ("WEBVTT", "NOTE") or stripped.startswith(("NOTE", "STYLE")):
            continue
        if SRT_BLOCK_ID.match(stripped) and not turns:
            continue
        labelled = VTT_SPEAKER.search(stripped)
        if labelled:
            speaker = labelled.group(1).strip()
            stripped = VTT_SPEAKER.sub("", stripped).strip()
        else:
            colon = SPEAKER_LINE.match(stripped)
            if colon:
                speaker = colon.group(1).strip()
                stripped = colon.group(2).strip()
        if not stripped:
            continue
        if turns and turns[-1].speaker == speaker and turns[-1].t_end == times[0]:
            turns[-1].text += " " + stripped
            turns[-1].t_end = times[1]
        else:
            turns.append(
                TurnSpec(
                    seq=len(turns),
                    speaker=speaker,
                    text=stripped,
                    t_start=times[0],
                    t_end=times[1],
                )
            )
    return turns


def _parse_srt(raw: str) -> list[TurnSpec]:
    return _parse_vtt(raw)


def _cue_times(line: str) -> tuple[float | None, float | None]:
    parts = line.split("-->")
    if len(parts) != 2:
        return None, None
    return _timestamp(parts[0]), _timestamp(parts[1])


def _timestamp(raw: str) -> float | None:
    bits = re.split(r"[:.,]", raw.strip())
    try:
        if len(bits) == 4:
            h, m, s, ms = (int(b) for b in bits)
        elif len(bits) == 3:
            m, s, ms = (int(b) for b in bits)
            h = 0
        else:
            return None
    except ValueError:
        return None
    return h * 3600 + m * 60 + s + ms / 1000


def _parse_csv(raw: str) -> list[TurnSpec] | None:
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows or len(rows[0]) < 2:
        return None
    header = [cell.strip().lower() for cell in rows[0]]
    if "speaker" in header and "text" in header:
        s_col = header.index("speaker")
        t_col = header.index("text")
        data = rows[1:]
    else:
        s_col, t_col = 0, 1
        data = rows
    turns = [
        TurnSpec(seq=i, speaker=str(row[s_col]).strip(), text=str(row[t_col]).strip())
        for i, row in enumerate(data)
        if len(row) > max(s_col, t_col) and str(row[t_col]).strip()
    ]
    return turns or None


def _docx_lines(path: Path) -> list[str]:
    from corpus_kb.partitioning import partition

    return [element.text or "" for element in partition(str(path))]

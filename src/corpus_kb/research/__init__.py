"""Research transcript ingestion (todo-11 (d)).

Parsers, role heuristics, exchange linking, and the dynamic-ingest ledger
for the event-sourced research domain. txt is first-class; vtt/srt/csv/docx
ride the same turn-recovery approach (regex/format parsers for speaker labels
and timestamps — v5 §2 "Existing transcript" row). No audio/video, no new
parser libraries (guardrail).
"""

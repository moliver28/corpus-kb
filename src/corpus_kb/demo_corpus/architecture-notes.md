# Architecture notes

A plain-language summary of how Corpus-KB fits together. This file is
sample content, and it is also true.

## The four surfaces

One server answers on four doors. Editors connect over MCP stdio.
Scripts call the HTTP API. Local automation uses the JSON-RPC socket.
People use the browser pages. Every operation returns the same envelope
on every surface.

## The pipeline

Ingest runs five stages: partition, chunk, embed, extract, store. A
pluggable strategy picks the chunking rules per file type. Markdown
splits at headings. Code splits at syntax boundaries. Transcripts split
at speaker turns.

## The storage

Postgres holds everything. Vectors live in a pgvector column. Keywords
go through full-text search. Entities and relations form a knowledge
graph. Row-level security scopes every table to one tenant.

## The search

Search blends two arms. The vector arm finds meaning; the full-text arm
finds exact words. Reciprocal rank fusion merges the two lists into one.
A reranker can reorder the fused list when it is enabled.

## The honesty rules

The system never hides a problem. A missing model degrades to flagged
vectors. A missing answer earns an "empty" status, not a guess. Health
checks, receipts, and logs report what actually happened.

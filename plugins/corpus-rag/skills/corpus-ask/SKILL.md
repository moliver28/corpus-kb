---
name: corpus-ask
description: Answer questions with citations from one study's corpus. Use when the user asks a question about their documents, reports, interviews, transcripts, or research files, wants sourced answers, or says "ask my corpus". Search first when the question is vague.
---

# Corpus Ask

Answer a question from one study's corpus with citations. A study is one
slug with one database. The corpus is the documents the user ingested
into that study.

## Steps

1. Pick the study. Pass `-p <slug>` when you know it. Otherwise the
   active study answers: set it once with `corpus-rag use <slug>`, or
   list studies with `corpus-rag list`. A folder's
   `.claude/corpus.json` may name the study too.
2. Check the question is clear. If it is vague, run a search first:

   ```bash
   corpus-rag search "<topic words>" -p <slug>
   ```

   Use the hits to sharpen the question.
3. Ask the engine:

   ```bash
   corpus-rag ask "<the user's question>" -p <slug>
   ```

4. Read the answer. It arrives with source citations. Report the
   citations with the answer. Show the file or chunk the citation names.
5. If the answer says the corpus has no matching content, say that
   plainly. Do not invent sources. Offer to ingest more files (see
   corpus-ingest).

## Notes

- The plugin starts the study's server when it is down; the first
  command may take a few seconds longer.
- The engine must be installed and reachable. Run `corpus-rag status` to
  check. See the plugin README for setup help.
- Degraded mode: without the embedding service the engine still searches
  with keywords. Answers may miss some sources. Say so when you see the
  degraded notice in the output.
- Do not call bare `corpus-kb ask` or `corpus-kb search`. They hit the
  default database, not the study, and a stale `corpus-kb` can sit first
  on PATH.

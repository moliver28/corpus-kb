---
name: corpus-ask
description: Answer questions with citations from the user's document corpus. Use when the user asks a question about their documents, reports, interviews, transcripts, or research files, wants sourced answers, or says "ask my corpus". Search first when the question is vague.
---

# Corpus Ask

Answer a question from the user's corpus with citations. The corpus is the
documents the user ingested with Corpus-KB.

## Steps

1. Check the question is clear. If it is vague, run a search first:

   ```bash
   corpus-kb search "<topic words>"
   ```

   Use the hits to sharpen the question.
2. Ask the engine:

   ```bash
   corpus-kb ask "<the user's question>"
   ```

3. Read the answer. It arrives with source citations. Report the citations
   with the answer. Show the file or chunk the citation names.
4. If the answer says the corpus has no matching content, say that plainly.
   Do not invent sources. Offer to ingest more files (see corpus-ingest).

## Notes

- The engine must be installed and reachable. Run `corpus-rag status` to
  check. See the plugin README for setup help.
- Degraded mode: without the embedding service the engine still searches
  with keywords. Answers may miss some sources. Say so when you see the
  degraded notice in the output.

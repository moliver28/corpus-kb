---
name: corpus-analyze
description: Pick and run the right analysis for a research question. Use when the user is unsure which statistical or text analysis to run, asks "which test", mentions significance, correlation, or methods confusion, or pastes a methods section.
---

# Corpus Analyze

Help a novice pick the right analysis and understand the result. The engine
has a method selector; this skill wraps it in plain language.

## Steps

1. Collect the question in one sentence. Ask the user if it is unclear.
2. Run the router with the question:

   ```bash
   corpus-rag op analysis_route --question "<question>"
   ```

   The answer lists ranked methods. Each row has `method_id`, `name`,
   `why`, and `run_hint`. Present the ranked rows as a short list in
   plain English.
3. Read the `status` field on the answer. When it says `degraded`, tell
   the user routing ran in degraded mode. Do not claim a confident
   winner then.
4. Explain the top rows in plain English. One short paragraph each:
   what the method does, what data it needs, what the result will mean.
5. Ask the user to confirm a method. On confirm, run it with its run
   hint:

   ```bash
   corpus-rag op <run_hint.op> <run_hint.params>
   ```

   Read the exact parameter names first with `corpus-rag ops` when the
   hint is short. Do not guess parameter names.

## Notes

- The user may say "I don't know what analysis to run". That is the main use
  of this skill. Start from their goal, not from method names.
- Keep claims about the data honest. If the corpus lacks the data a method
  needs, say so and propose ingesting more files (see corpus-ingest).

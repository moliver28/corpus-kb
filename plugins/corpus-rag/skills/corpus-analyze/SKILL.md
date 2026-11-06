---
name: corpus-analyze
description: Pick and run the right analysis for a research question. Use when the user is unsure which statistical or text analysis to run, asks "which test", mentions significance, correlation, or methods confusion, or pastes a methods section.
---

# Corpus Analyze

Help a novice pick the right analysis and understand the result. The engine
has a method selector; this skill wraps it in plain language.

## Steps

1. Collect the question in one sentence. Ask the user if it is unclear.
2. Ask the engine to name the method:

   ```bash
   corpus-kb analysis-select-method
   ```

   Follow the engine's prompt for the question text. The output names a
   method and says why it fits.
3. Explain the choice in plain English. One short paragraph: what the method
   does, what data it needs, what the result will mean.
4. Suggest the next question the user can ask. Offer to run the method.
5. If the engine names more than one method, present them as a short list.
   Say what each one answers and let the user pick.

## Notes

- The user may say "I don't know what analysis to run". That is the main use
  of this skill. Start from their goal, not from method names.
- Keep claims about the data honest. If the corpus lacks the data a method
  needs, say so and propose ingesting more files (see corpus-ingest).

---
description: "Keyphrase candidates ranked three ways (tfidf, embedding, llm)"
argument-hint: "[--variant tfidf|embedding|llm] [--limit N]"
---
# corpus-keyphrases-extract — analysis method card

## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: themes (methods 05, 06, 07).
3. Check the inputs it needs: raw_text (the embedding variant also uses embeddings; the llm variant uses an LLM).
4. Budget the calibration effort (low to moderate) and autonomy (automated_with_audit).
5. Read the cautions. Run. Then do the validation step.

## Run

  ```
  corpus-kb keyphrases-extract [--variant tfidf|embedding|llm] [--limit N]
  ```

- Cautions: rare wording can rank high without research importance. The tfidf arm is sensitive to boilerplate; the embedding arm favors model training language.
- Validation: review tokenization, common words, phrase lengths, and the top-ranked terms by hand.
- Claim only: descriptions of distinctive wording in the corpus.
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb keyphrases-extract --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).

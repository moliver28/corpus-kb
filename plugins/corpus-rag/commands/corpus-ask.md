---
description: Ask a question about one study's corpus and report the answer with citations. Uses the corpus-ask skill.
---

Ask a question with citations from a study's corpus.

Follow the `corpus-ask` skill. Take the question from the conversation.
Pick the study (a `-p <slug>` the user names, the active study, or the
folder's `.claude/corpus.json`). Run `corpus-rag ask "<question>" -p
<slug>`, and report the answer with its citations. If the question is
vague, run `corpus-rag search` first to sharpen it.

$ARGUMENTS

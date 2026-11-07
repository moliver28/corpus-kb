---
name: corpus-codify-study
description: Start a chunk-coding study. Use when the user says "code my interviews", "build a codebook", "thematic analysis", or "deductive coding", wants to pick a paradigm, provision a study for coding, or load a codebook.
---

# Corpus Codify Study

Set up one study for chunk-level coding: pick the paradigm, provision,
ingest the corpus, and load a codebook. The coding work itself continues
in the corpus-codify-code skill.

## Steps

1. Ask three questions before any command:
   - What is the study called? It becomes the slug.
   - Where does the corpus come from? Name the files or folders.
   - Which paradigm? Codebook (deductive) or reflexive.
2. Explain the choice in plain words. Codebook coding starts from named
   codes with definitions, codes every chunk against them, and measures
   agreement between coders. Reflexive thematic analysis lets codes
   emerge while reading; coders are expected to differ, so agreement
   numbers do not apply. Show this caution when the user weighs them:

> **Caution:** alpha and kappa measure how much coders agree. They do not say whether a code is true, whether the quotes are honest, or whether the codebook fits the corpus. A high number can hide a shared mistake; a low number can mean the material is rich. Treat the number as one input; the judgment stays with you.

3. Provision with the paradigm:

   ```bash
   corpus-rag codify provision <slug> --paradigm codebook
   ```

   For a reflexive study use `--paradigm reflexive`. On an existing
   study the command records the paradigm; it never provisions twice.
4. Ingest the corpus:

   ```bash
   corpus-rag ingest "<path>" -p <slug>
   ```

   Then check what landed with `corpus-rag status -p <slug>`.
5. Load an existing codebook when the user has one:

   ```bash
   corpus-rag codify load-codebook codebook.json -p <slug>
   ```

   The command prints the codebook version id. Keep it; every later
   stage (pool, code, report, release) hangs off that version.
6. When the user has no codebook, build one first. Run the guided
   interview in `../../references/codebook-rules.md`: one code at a
   time, all six MacQueen fields, with a worked example. Write the
   codebook JSON, show it to the user, and load it as in step 5.
7. Say what is on record before you stop: the study slug, the paradigm,
   the ingest counts, and the codebook version id.

## Notes

- A codebook file holds a JSON object with a `codes` list. Each code
  carries the six MacQueen fields; the engine validates and embeds them.
- The paradigm is a property of the study, not of one command. `codify
  reliability` refuses for reflexive studies before it touches a server.
- Do not re-ingest the same files with `force=true`; it orphans rows.
  Ingest skips files it has already seen by content hash.

---
name: corpus-codify-code
description: Run chunk coding on a study. Use when the user says "run the coder", "continue coding", or "check reliability", wants to pool candidates, build a cells file, code a batch, calibrate floors, check saturation, or measure agreement.
---

# Corpus Codify Code

Code chunks against a loaded codebook: pool candidates, build cells,
code small batches, and measure how well the coding holds up. The study
setup lives in the corpus-codify-study skill; reports and exports live
in the corpus-codify-export skill.

## Steps

1. Pool the corpus for the loaded codebook:

   ```bash
   corpus-rag codify pool -p <slug>
   ```

   Read `keyword_governance` (collisions, over-cap keywords) and
   `speaker_roles` before you continue; they say what pooling saw.

2. Build the next cells file for one coder:

   ```bash
   corpus-rag codify cells -p <slug> --coder gpt-x
   ```

   The file holds at most five candidate codes per cell, best first.
   Chunks with no pool signals cannot be coded; the command says how
   many and points back to `codify pool`.

3. Code a small batch first. Cut the cells file down to a handful of
   cells, then run:

   ```bash
   corpus-rag codify code --cells cells-small.json -p <slug> --coder gpt-x --batch-id batch-001
   ```

   Read the routing results. The bands are 0.90 (high), 0.70 (mid),
   and 0.50 (low); see `../../references/reliability-and-routing.md`
   for what each route means. The coding rules in
   `../../references/coding-rules.md` (decision shape, participant
   words, anti-priming) explain what the coder must produce per cell.

4. Check the coding status:

   ```bash
   corpus-rag codify status -p <slug>
   ```

5. When a code under-captures or over-captures, calibrate its floors
   from labeled chunks, then pool again:

   ```bash
   corpus-rag codify calibrate-floors labels.json -p <slug>
   corpus-rag codify pool -p <slug>
   ```

6. Before a big batch, check saturation:

   ```bash
   corpus-rag codify saturation -p <slug> --batch-id batch-001
   ```

   Saturation is a provisional signal. It says the new-code rate fell
   below the threshold for this batch; it does not say the corpus is
   exhausted.

7. Check reliability when enough cells are double-coded:

   ```bash
   corpus-rag codify reliability -p <slug>
   ```

   The command refuses for reflexive studies before it touches a
   server; that refusal is correct, not a fault. For codebook studies,
   read the alpha and kappa against the thresholds in
   `../../references/reliability-and-routing.md`, and show this caution
   beside every number you report:

> **Caution:** alpha and kappa measure how much coders agree. They do not say whether a code is true, whether the quotes are honest, or whether the codebook fits the corpus. A high number can hide a shared mistake; a low number can mean the material is rich. Treat the number as one input; the judgment stays with you.

8. Repeat steps 2 to 7 until saturation holds and reliability clears
   the thresholds, then move to the corpus-codify-export skill.

## Notes

- Keep one coder tag per coder (human or model). Reliability compares
  coder tags, so mixed tags blur the measurement.
- Never hand-edit `final_codes` or `chunk_codes`; routing writes them.
  A human override goes through the review stage of the research
  pipeline, not through SQL.
- `corpus-rag events -p <slug>` and `corpus-rag doctor -p <slug>` tell
  you whether the engine itself is healthy before you blame the coding.

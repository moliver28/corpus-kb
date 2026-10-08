---
description: Run the research cycle with in/on/out-of-loop postures and halt gates
argument-hint: --mode in|on|out [--guide] [--watch <dir>]
---
# corpus-research-cycle — the full pipeline with human-critical gates

Chain ingest -> inductive -> deductive -> keywords -> report without
writing a step script. The cycle HALTS at human-critical gates -
`codebook_promotion` is hard-floored (promotion is never automated) -
prints `AWAITING HUMAN: <gate>` plus the exact next command, and resumes
when you re-run it after acting on the gate.

Postures:

- Fully in the loop - exactly ONE stage per call:

  ```
  corpus-kb research cycle --mode in
  ```

- On the loop - one stage, then it asks before continuing:

  ```
  corpus-kb research cycle --mode on [--guide]
  ```

- Out of the loop - unattended except at the halt gates:

  ```
  corpus-kb research cycle --mode out --dir <drop-dir> --question "<q>" [--guide]
  ```

- Watch a drop directory in the foreground (re-arms on new files):

  ```
  corpus-kb research cycle --mode out --watch <dir>
  ```

- `--guide` turns every halt into a taught decision (proposed codes,
  example units, promote-vs-skip consequences); `--json` emits one
  stage/gate event per line for agents.
- All flags: `corpus-kb research cycle --help`

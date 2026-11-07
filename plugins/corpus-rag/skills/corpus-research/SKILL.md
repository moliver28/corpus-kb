---
name: corpus-research
description: Run the research pipeline on one study. Use when the user wants to ingest transcripts for a study, propose themes, promote a code into the codebook, run deductive coding, review assignments, build a research report, ask the notebook, plan a release, or run the narrated demo.
---

# Corpus Research

The research pipeline is event-sourced and human-gated. Each stage is
one op, sent through the op runner. The engine records every stage as
events, so health checks come from the event store.

## Stage map

Pick the study first (`-p <slug>`), then send the stage's op:

| Stage | Command |
|---|---|
| Ingest transcripts | `corpus-rag op research_ingest -p <slug> path="<path>" project_id=interviews-2026` |
| Ingest one transcript | `corpus-rag op research_ingest_transcript -p <slug> file_path="<path>" project_id=interviews-2026` |
| Propose themes (inductive) | `corpus-rag op inductive_run -p <slug> project_id=interviews-2026` |
| Promote a code (human gate) | `corpus-rag op codebook_promote -p <slug> proposed_id=<id> name="Trust in vendor" definition="The participant expects the vendor to deliver."` |
| Code (deductive) | `corpus-rag op coding_run -p <slug> codebook_version_id=<id> project_id=interviews-2026` |
| Review (human gate) | `corpus-rag op review_execute -p <slug> assignment_id=<id> decision=accept reviewer=ana` |
| Report | `corpus-rag op research_report -p <slug> level=novice` |
| Ask the notebook | `corpus-rag op notebook_ask -p <slug> question="<question>" project_id=interviews-2026` |
| Evidence for one code | `corpus-rag op notebook_evidence -p <slug> code=trust project_id=interviews-2026` |
| Missing-code radar | `corpus-rag op notebook_uncoded -p <slug> project_id=interviews-2026` |
| Code overlap | `corpus-rag op notebook_overlap -p <slug> project_id=interviews-2026` |
| Full cycle | `corpus-rag op research_cycle -p <slug> mode=in` |
| Narrated demo | `corpus-rag op research_demo -p <slug>` |

Release stage (each op needs a `codebook_version_id`; read the exact
arguments first with `corpus-rag ops --family release`):

| Stage | Op |
|---|---|
| Check readiness | `release_status` |
| Assess a version | `release_assess` |
| Request a release | `release_request` |
| Record a review | `release_review` |
| Execute (human gate) | `release_execute` |
| Compare two releases | `release_compare` |

The op inventory is live. `corpus-rag ops` lists every op with its
arguments; do not guess parameter names.

## Rules the pipeline runs on

1. Pick one `project_id` per study and pass it on every call. Mixing
   project ids splits the evidence.
2. Four decisions belong to a human. The agent asks; it never decides:
   - a code definition (what a code means, when it applies);
   - a reviewer decision (accept or override an assignment);
   - the release approver;
   - whether to continue past a halted cycle gate.
3. Read the envelope `status` on every answer. When it says `degraded`,
   say the word "degraded" out loud to the user and name the missing
   piece (usually the embedding service).
4. Never edit the read tables. The engine owns every write through its
   handlers.
5. Never re-ingest with `force=true`. It creates a second copy of the
   same file and orphans the first one's rows today.
6. Health comes from the event store and the doctor:

   ```bash
   corpus-rag events -p <slug>
   corpus-rag doctor -p <slug>
   ```

## The demo walk

When the user wants to see the pipeline without a corpus, run the
demo. It narrates each stage on the bundled demo corpus:

```bash
corpus-rag op research_demo -p <slug>
```

Read the narration, then carry the same stage order to a real study.
The demo ends before a release review; a real release review is a
human decision on a real codebook version.

## Reports carry agreement numbers

`research_report` answers with kappa and alpha fields. Show this
caution beside every agreement number you report:

> **Caution:** alpha and kappa measure how much coders agree. They do not say whether a code is true, whether the quotes are honest, or whether the codebook fits the corpus. A high number can hide a shared mistake; a low number can mean the material is rich. Treat the number as one input; the judgment stays with you.

For the chunk-coding path (pool, cells, reliability, export) use the
corpus-codify skills instead. They share this caution.

## Notes

- The plugin starts the study's server when it is down; the first
  command may take a few seconds longer.
- `research_cycle` and `research_demo` are long-running and synchronous:
  the command answers when the run finishes or halts.
- Promotion stays an explicit human act on every surface. The engine
  never automates it.

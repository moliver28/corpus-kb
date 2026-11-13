---
name: corpus-uxr
description: Run one UX research study from new transcripts to themes.md with checked quotes and a deterministic rollup. Use when the user asks to analyze a UXR study, check whether a hypothesis is supported, or asks what was learned from interviews.
disable-model-invocation: true
---

# Corpus UXR

Run one UX research study end to end. Everything the model writes stays
clearly marked. Quote-checked rows, not fresh model guesses, decide the
report. Local-only consent means no study text goes to a cloud model.

All commands below start with `corpus-rag uxr` plus a subcommand, and
run from the study folder. Add `-p <slug>` when the folder names no
study. `<path>` below means the study folder.

## Retrieve first

1. Check consent, then retrieve what is stored before any new analysis:

   ```bash
   corpus-rag uxr consent
   corpus-rag uxr insights -p <slug>
   ```

2. Answer from the stored rows. Cite as `[S:<source-slug>]`. Say when a
   question has no rows yet.

## Analyze from new transcripts

Run the steps in order. Show `unmatched` files to the researcher and
never guess a class. Never run analysis from a hook.

1. **Plan.** Store the study and its question ids:

   ```bash
   corpus-rag uxr ingest-plan --spec UX1-275.spec.json
   ```

2. **Consent.** `corpus-rag uxr consent` again if the session changed.
   Exit 1 (local only) means no transcript text goes to a cloud model
   at any later step.
3. **Classify.** `corpus-rag uxr classify --dir <path>` with the
   `--roster` and `--staff` name files when the researcher has them.
   Classes: `participant_session`, `internal_meeting`, `plan`,
   `derived`, `excluded`, `unmatched`. Show `unmatched` to the
   researcher; never guess.
4. **Confirm speakers** with the researcher, then store the classes:

   ```bash
   corpus-rag uxr confirm-classes '<json>'
   ```

   The JSON is a list of `{"path": ..., "class": ...}` objects, or an
   `@file`. Only `participant_session` and `internal_meeting` reach the
   source list.
5. **Drain.** `corpus-rag drain -p <slug>` sends the pending queue.
6. **Stubs.** `corpus-rag uxr stubs --dir <path>` writes
   `sources/corpus/` stubs so `[S:slug]` cites resolve. Stubs never
   overwrite.
7. **Pilot.** Analyze 2 sessions with a small model. Report tokens and
   cost before the full run.
8. **Per-session analysis.** One small-model call per session. Output
   JSON rows only: `session`, `unit_id`, `stance`, `quote`,
   `unit_text`, `rationale`. Quotes stay 25 words or fewer. Send at
   most the top 8 units per question.
9. **Validate and store.** `corpus-rag uxr validate-rows @rows.json
   --store`. Failed rows store as `unverified`. Near misses are logged.
   Retry failed rows once with a larger model.
10. **Summaries.** `corpus-rag uxr apply-summary --dir <path>` writes a
    marked block into each `interviews/<stem>-summary.md`. Question ids
    a researcher line covers are skipped.
11. **Rollup.** `corpus-rag uxr rollup --dir <path>`. Exit 1 means too
    many unverified rows; stop and show the researcher.
12. **Contract.** `corpus-rag uxr contract-test --evidence-file <f>`,
    or the `uxr_report_tool` userConfig option. Exit 1 means the report
    tool output differs; do not write the report.
13. **One narrative call.** Write the themes narrative with one model
    call over the rollup and the checked rows.
14. **Themes.** `corpus-rag uxr write-themes --narrative-file <f>
    --dir <path>`. The command writes only between the `corpus:begin`
    and `corpus:end` markers in `analysis/themes.md`. It never opens
    `analysis/codes.md`.

## Notes

- Stance enums: `rq` free text or "No direct evidence this session.";
  `h` Supports, Challenges, Mixed, No direct evidence; `a` Holds,
  Breaks, Untested.
- A quote must be `exact` or `near_miss` (0.90 or more) against its
  unit text. A `fail` row stores as `unverified`.
- `corpus-rag uxr import` copies 0.6.0 rows once. It reads the engine
  database through the read-only SQL route and never writes to it.

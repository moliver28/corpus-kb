---
description: "Release lifecycle: assess, request, review, release, status, compare"
argument-hint: "--codebook-version-id <uuid> [--release-id <uuid>] [--approver <you>]"
---
# corpus-release — the release lifecycle (v6 §6)

Walk a codebook version from draft to released codebook: assess the gates,
open the draft, review the packet, execute with a named approver, and diff
successors.

- Compose EVERY gate over current evidence (writes NOTHING; not_evaluable
  gates are reported, never silently passed):

  ```
  corpus-kb release assess --codebook-version-id <uuid> [--profile <name>]
  ```

- Open a draft_candidate release pinned to the current content hash:

  ```
  corpus-kb release request --codebook-version-id <uuid> --creator <you>
  ```

- The review packet: gate evidence, failing/not_evaluable gates, waivers,
  and the exact next commands:

  ```
  corpus-kb release review --codebook-version-id <uuid> --release-id <release-id>
  ```

- Execute the release (HUMAN APPROVER required; enforcement=enforce blocks
  unmet required gates, `--waive GATE=justification` records explicit
  waivers; a refusal exits 1 with a structured error):

  ```
  corpus-kb release release --codebook-version-id <uuid> --release-id <release-id> --approver <you>
  ```

- Read the trail; every command takes --json:

  ```
  corpus-kb release status --codebook-version-id <uuid>
  corpus-kb release compare --codebook-version-id <uuid> --old-release-id <a> --new-release-id <b>
  ```

- All flags: `corpus-kb release status --help` (and each subcommand)

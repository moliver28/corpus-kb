# Getting started with qualitative coding in Corpus-KB

This page is for researchers who have never run an automated coding pipeline.
Read it once top to bottom, run the demo, then point the same commands at your
own transcripts.

## What is qualitative coding?

Qualitative coding is the practice of reading interview or focus-group
transcripts and labeling passages with **codes** - short, defined categories
such as "transportation barriers" or "cost concerns". Traditionally a human
reads every line and applies codes by hand. Corpus-KB automates the reading
and the first-pass labeling, and keeps humans in charge of every decision
that defines or changes the codebook.

A **codebook** is the authoritative set of codes: each code has a name, a
definition, inclusion/exclusion criteria, and (for automated coding) gold
exemplar passages. Codebooks are versioned; every promotion creates a new
immutable version.

## Deductive vs inductive

- **Deductive coding** starts from a codebook you already believe in and asks
  "which passages match which code?" Corpus-KB scores every codable unit
  against the codebook in three views (the answer alone, question+answer, and
  the question alone) with per-code calibrated thresholds, then routes
  uncertain calls to review. Run it with `corpus-kb coding run`.
- **Inductive coding** starts without a codebook and asks "what themes are
  in this corpus?" Corpus-KB summarizes and clusters units (UMAP/HDBSCAN,
  optional extra) and proposes candidate codes. Proposed codes are NEVER
  auto-promoted; you promote them with `corpus-kb codebook promote`.

A typical project alternates: an inductive pass proposes codes, you promote
the good ones, then deductive runs apply the grown codebook across the corpus.

## Why human gates exist

Automated coding is fast but fallible, and a codebook encodes your research
judgment. Corpus-KB therefore halts for humans at the points where a wrong
automated step would silently corrupt the research record:

- **Codebook promotion** is always a human command. A proposed code becomes a
  code only when you name it, define it, and pass the duplicate gate.
- **Thresholds fitted on fewer than 20 gold exemplars per code are flagged
  unreliable**, and unreliable codes route everything to review instead of
  auto-assigning.
- **Review decisions are explicit.** Every model assignment can be accepted
  (`corpus-kb review accept`) or overruled (`corpus-kb review override`);
  overrides feed threshold refits and the G3 audit.

## Run the demo

The fastest way to see the whole pipeline is the narrated demo. It bundles a
two-interview mini corpus and a small codebook under `docs/demo-corpus/` and
walks through ingest, coding, review, the governance report, and a cited
notebook answer, explaining each stage as it goes (~5 minutes):

```bash
corpus-kb research demo
```

No arguments are needed beyond `corpus-kb setup`. Re-runs are safe: file and
text hashes make already-ingested files no-ops.

## Ingest your first transcript

Real transcripts go in as files (`.txt` first-class, plus `.vtt`, `.srt`,
`.csv`, `.docx`). A plain-text transcript just needs `Speaker: text` lines:

```bash
# one file
corpus-kb research ingest-transcript interviews/dana.txt

# a whole folder, watched for new drops
corpus-kb research ingest interviews/ --watch
```

Ingest parses speaker turns, maps roles heuristically (the question-asker
becomes the moderator), links question-answer exchanges, and records
everything as events; projections build the read models. Re-runs process
only new or changed files.

## Build a codebook

Two ways to get a codebook version:

1. **Inductive first pass** (optional `inductive` extra):
   `corpus-kb coding inductive` clusters the corpus and proposes codes; then
   promote with:
   `corpus-kb codebook promote --proposed-id <id> --name "Code name" --definition "..."`.
2. **Direct seeding** from your own gold exemplars, as the demo does: codes
   need gold exemplar passages (quoted verbatim from your transcripts) so the
   deductive run can build prototypes.

For reliable automated thresholds you want at least 20 gold exemplars per
code; below that, coding still runs but routes to review (see the demo).

## Run coding

```bash
corpus-kb coding run --codebook-version-id <uuid> [--project-id <uuid>]
```

The summary reports explicit / question-dependent / review splits. Assignments
land in the review queue whenever thresholds are gray, ambiguous, or
unreliable.

## Review assignments

```bash
corpus-kb review accept <assignment-id> --reviewer "your-name"
corpus-kb review override <assignment-id> --reviewer "your-name" --note "why"
```

## Ask the notebook

```bash
corpus-kb research ask "How do transportation problems shape food access?" \
  --project-id <uuid>
```

Every answer sentence carries an exchange-level citation (document, speaker,
timestamp/turn range). Add `--retrieval-only` for evidence without generation.

## Report and iterate

```bash
corpus-kb research report --level novice
```

Then read [understanding-your-report.md](understanding-your-report.md) for a
plain-language guide to every field. The full research command set, including
the orchestrated cycle (`corpus-kb research cycle`), is documented in
[research.md](research.md).

# UI demo: 10-minute investor walkthrough

This is the live script for the `/app/` SPA. Every step was executed and timed
against a fresh throwaway stack (2026-10-27; see "One-time demo-machine prep").
Plain English only. Each step has a fallback line for the worst case.

## One-time demo-machine prep

1. Fresh database: `CORPUS_KB_TEST_DB=corpus_kb_demo python tests/research_db.py`
   bootstrap (creates a scratch DB; the app role owns it; AGE/pgml migrations skip).
2. Start the server: `CORPUS_KB_DATABASE_URL=<demo dsn> corpus-kb start --transport http --port 8010`.
3. Preload the sample library: `POST /api/demo/load` (idempotent).
4. Generate a demo folder: `python scripts/gen_demo_corpus.py --count 12 --out <folder>`.
5. Topics beat: set `topics.enabled: true` and install the `[topics]` extra.
   Without it the button honestly reports "disabled" with the remedy (fix landing in PR-6).
6. Verified-answer beat (optional): serve `qwen3-reranker:4b` (llama-server recipe in
   `docs/ADMIN.md`). Without a reranker, every answer honestly shows "not verified".
   The claim-support number shows too. That is the designed degraded state, not a bug.
7. Warm-up BEFORE the room fills: one ask (embeddings + one generation, ~90 s cold).

## The script (8 steps, ~10 minutes)

| # | Step | Expected | Time | Fallback line |
|---|------|----------|------|---------------|
| 1 | Open `http://<host>:8010/app/` | Setup page; system status fills in | ~5 s | "This page checks the server on its own." |
| 2 | Setup: read the detailed checks | Green table; honest Warning rows show the fix | ~10 s | "It tells you what is missing and how to fix it." |
| 3 | Ingest → Server path: paste the demo folder, Run, confirm | Confirm gate → running → Done receipt | ~20 s | "You can stop waiting at any time; the server keeps working." |
| 4 | Ask: "How does ingestion work?" | Cited answer; hover a citation → linked sentences highlight | ~90 s | "Every sentence points at its source." |
| 5 | Ask: "Does the corpus prove my product doubles revenue?" | Honest "not verified" + claim-support number | ~90 s | "It says so when it cannot verify a claim." |
| 6 | Analyze: advisor "Group documents into topics and show topic sizes" → Run "Build topics" | Ranked advisor table with claims guidance; topics receipt | ~60 s | "The advisor explains why each method ranks where it does." |
| 7 | Review: topics table; Scale: run ledger | Topics rows; ledger polling live (~2 s cadence) | ~30 s | "The ledger checks on its own while the tab is open." |
| 8 | Codebook: promote gate; Release: assess with an unknown id | Human gate modal; structured refusal, never a crash | ~60 s | "Human gates stay human; refusals name the failing gates." |

Recorded walkthrough (2026-10-27, throwaway stack, qwen3-embedding:4b + qwen3:4b):
- App load 2.4 s. Folder ingest of 12 files ~18 s. Cited answer ~73 s.
- Topics build was disabled in that stack's config. The card showed the honest
  disabled notice (that beat needs prep step 5).
- Layout held at 1024 px. A server restart reloaded the SPA in ~1.5 s on the
  same stage.

## Risk drills (all passed live)

- **Model server killed mid-answer**: within ~10 s the Ask card showed
  "Answer generation is unavailable. Showing the source text without an answer."
  with the evidence list. No stack trace, no fake answer.
- **Window shrunk to 1024 px**: no horizontal overflow; all eight stage items
  stay visible.
- **Server killed and restarted**: the SPA reloads (~1.5 s) and stays on the
  same stage.

## Demo-day hygiene

- Warm-up run before the room fills (step 7 of prep), timed.
- Screen-record the full run as the backup; save it next to this file.
- String audit on demoed screens: no config keys, exit codes, or status words in
  the money path. (Doctor's detail table and Analyze dormant cards show config
  keys by design; they are not demoed beats.)
- Known honest states: not-wired banners for operations still missing manifest
  rows (issue #182). Also "not verified" badges without a reranker.

## Verified-live caveats

- The header menu was verified via DOM event dispatch. An automation-browser
  quirk blocked synthetic trusted clicks. Do one manual click-through of the
  stage menu before the room fills.
- Citations showed full local file paths; the basename fix landed in PR-6.

# Plan: Optimize Installation Process

**Goal:** A novice with zero coding experience can ask Claude Code in one shot to install Corpus-KB and get it fully operational, with Claude explaining the system afterward. README must also be readable by a human (no AI-speak).

---

## Changes Required

### 1. `config.yaml` — Default to lightweight model
**Why:** Current default `qwen3-embedding:8b-q8_0` is 8GB. A novice needs something that downloads in seconds.
**Change:** Default to `nomic-embed-text` (274MB) with `dimension: 768`. Add comment explaining power-user upgrade path.

### 2. `config.yaml` — Fix key name
**Why:** The config uses `dimension` (singular) but server.py reads `dimensions` (plural). This would silently use the 768 fallback.
**Change:** `dimension` → `dimensions`.

### 3. `pyproject.toml` — Add project URLs, fix metadata
**Why:** Clean up for a professional feel. Add classifiers, URLs, python version spec.

### 4. `scripts/setup.sh` — macOS-first bulletproof rewrite
**Why:** Current script assumes Python 3.11+ and Ollama are already installed. Needs to auto-install everything on a bare Mac (and Linux).
**Changes:**
- Auto-install Homebrew if missing
- Auto-install Python 3.11 via Homebrew if missing
- Auto-install Ollama via Homebrew if missing
- Pull nomic-embed-text model
- Create venv, pip install
- Set up MCP configs for Claude Code, Cursor, VS Code
- Create sample files
- Run demo smoke test
- Print final instructions

### 5. `scripts/setup.ps1` — Windows-first bulletproof rewrite
**Why:** Current script assumes Python and Ollama are installed.
**Changes:**
- Auto-install Python via winget/installer if missing
- Auto-install Ollama via winget if missing
- Same steps as setup.sh

### 6. `CLAUDE.md` — One-shot AI instructions
**Why:** A file that Claude Code reads to know exactly how to install and explain the system.
**Content:**
- Summary of what Corpus-KB is
- Step-by-step installation instructions Claude can execute
- Post-install explanation script (Claude reads this to the user)
- Troubleshooting guide

### 7. `README.md` — Human-readable rewrite
**Why:** Current README is too terse. Needs to be welcoming to non-coders, explain the system clearly, show what's possible.
**Structure:**
- Title + brief description (no AI jargon)
- Quick demo GIF / visual walkthrough
- What you can do with it (use cases)
- How it works (simple explanation)
- Installation (two paths: one-shot with Claude Code, or manual)
- Configuration (model, storage, etc.)
- MCP client setup
- Architecture diagram (ASCII)
- Development / contributing

### 8. `scripts/demo.py` — Fix model reference
**Why:** Hardcodes `qwen3-embedding:8b-q8_0` but setup installs `nomic-embed-text`.
**Change:** Use config/model defaults, not hardcoded qwen3.

---

## Execution

| # | File | Change | Agent |
|---|------|--------|-------|
| A | `config.yaml` | Default model → nomic-embed-text, fix key name | Direct edit |
| B | `pyproject.toml` | Add metadata, URLs | Direct edit |
| C | `scripts/setup.sh` | macOS-first, auto-install everything | Agent 1 |
| D | `scripts/setup.ps1` | Windows-first, auto-install everything | Agent 1 |
| E | `scripts/demo.py` | Fix model reference | Direct edit |
| F | `CLAUDE.md` | One-shot AI instructions | Agent 2 |
| G | `README.md` | Human-readable rewrite | Agent 3 |

**Parallel execution groups:**
- Group 1 (direct): A, B, E — config + metadata + demo fix
- Group 2 (agent): C, D — both setup scripts (one agent)
- Group 3 (agent): F — CLAUDE.md
- Group 4 (agent): G — README.md

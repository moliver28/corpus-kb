#!/usr/bin/env bash
# ==============================================================================
# Corpus-KB — Setup Script
# ==============================================================================
# Bootstraps the corpus-kb local RAG system on macOS or Linux.
# Idempotent — safe to re-run.  Requires Python 3.11+ and an internet
# connection for first-time setup.
#
# Usage:
#   bash scripts/setup.sh
#
# What it does:
#   1. Detects OS / architecture and installs platform prerequisites
#   2. Verifies Python ≥ 3.11 (guides installation if missing)
#   3. Creates a Python virtual environment at <repo-root>/.venv/
#   4. Installs corpus-kb in editable development mode (pip install -e .)
#   5. Installs Ollama (if missing), starts the service, waits for health
#   6. Pulls nomic-embed-text and verifies the embedding API works
#   7. Creates data directories and sample files for first-run
#   8. Rewrites MCP editor configs to use the absolute venv binary path
#   9. Runs the demo smoke test
#  10. Prints next steps for Cline / Claude Code / VS Code / Cursor
# ==============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
#  COLOURS & LOGGING HELPERS
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BLUE='\033[0;34m'; GRAY='\033[0;90m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GRAY}[INFO]${NC}  $1"; }
ok()    { echo -e "  ${GREEN}✓${NC}  $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $1"; }
fail()  { echo -e "  ${RED}✗${NC}  $1"; }
header(){ echo; echo -e "${CYAN}━━━ $1 ━━━${NC}"; }
sub()   { echo -e "  ${BLUE}→${NC}  $1"; }

# ---------------------------------------------------------------------------
#  PATHS
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
VENV_BIN="$VENV_DIR/bin"
VENV_PIP="$VENV_BIN/pip"
VENV_PYTHON="$VENV_BIN/python"
VENV_CORPUS_KB="$VENV_BIN/corpus-kb"

MCP_SRC="$PROJECT_ROOT/mcp-configs"

# ---------------------------------------------------------------------------
#  BANNER
# ---------------------------------------------------------------------------
echo -e "${CYAN}${BOLD}"
echo '╔══════════════════════════════════════════════════════════════╗'
echo '║                   Corpus-KB  Setup                          ║'
echo '║  Local RAG system — MCP tools for AI code editors           ║'
echo '╚══════════════════════════════════════════════════════════════╝'
echo -e "${NC}"
info "Project root : $PROJECT_ROOT"
info "Script       : $SCRIPT_DIR/setup.sh"
echo

# ---------------------------------------------------------------------------
#  TIMER
# ---------------------------------------------------------------------------
SECONDS=0  # bash built-in

# ---------------------------------------------------------------------------
#  OS DETECTION
# ---------------------------------------------------------------------------
header "1/11  Platform detection"

OS="unknown"
ARCH="$(uname -m)"
case "$(uname -s)" in
    Darwin*)  OS="macos" ;;
    Linux*)   OS="linux"  ;;
    *)        warn "Unsupported OS: $(uname -s). Proceeding with best-effort." ;;
esac

info "OS   : ${OS} ($(uname -s))"
info "Arch : ${ARCH}"
echo

# ---------------------------------------------------------------------------
#  INTERNET CONNECTIVITY CHECK
# ---------------------------------------------------------------------------
header "2/11  Internet connectivity"

check_internet() {
    # Try several well-known endpoints in case of regional DNS issues
    for url in "https://google.com" "https://github.com" "https://ollama.com"; do
        if curl -sf --max-time 5 "$url" > /dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

if check_internet; then
    ok "Internet reachable"
else
    fail "No internet connection detected."
    echo "  ${YELLOW}This script requires internet to:"
    echo "    - Install/update Homebrew (macOS)"
    echo "    - Install system packages (Linux)"
    echo "    - Install Python dependencies from PyPI"
    echo "    - Download Ollama and embedding models"
    echo "  Please check your connection and re-run.${NC}"
    exit 1
fi
echo

# ---------------------------------------------------------------------------
#  PLATFORM PREREQUISITES
# ---------------------------------------------------------------------------
header "3/11  Platform prerequisites"

# ------- curl (needed throughout) -------
if ! command -v curl &>/dev/null; then
    warn "curl not found — attempting to install ..."
    case "$OS" in
        linux)
            if   command -v apt &>/dev/null; then sudo apt update -qq && sudo apt install -y -qq curl
            elif command -v yum &>/dev/null; then sudo yum install -y curl
            elif command -v dnf &>/dev/null; then sudo dnf install -y curl
            else fail "No known package manager. Install curl manually."; exit 1
            fi
            ;;
        macos)
            # macOS ships curl by default — if missing, something is wrong
            fail "curl not found on macOS; this is unexpected. Install Xcode CLT or curl and re-run."
            exit 1
            ;;
    esac
    ok "curl installed"
else
    ok "curl available"
fi

# ------- macOS specific -------
if [[ "$OS" == "macos" ]]; then
    # --- Xcode Command Line Tools ---
    if ! xcode-select -p &>/dev/null; then
        info "Xcode Command Line Tools not found. Requesting install ..."
        xcode-select --install 2>/dev/null || true
        echo "  ${YELLOW}Please complete the Xcode CLT installation dialog, then re-run this script.${NC}"
        echo "  ${YELLOW}(Or run: sudo xcode-select --reset)${NC}"
        exit 1
    fi
    ok "Xcode Command Line Tools"

    # --- Homebrew ---
    if command -v brew &>/dev/null; then
        ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
        # Quiet auto-update only if needed
        if [[ -z "${HOMEBREW_NO_AUTO_UPDATE:-}" ]]; then
            export HOMEBREW_NO_AUTO_UPDATE=1
        fi
    else
        info "Installing Homebrew ..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add to PATH for the current session if Homebrew wasn't on PATH
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        ok "Homebrew installed"
    fi
fi

# ------- Linux specific -------
if [[ "$OS" == "linux" ]]; then
    # Build essentials and python3-venv are commonly needed
    local_missing=()
    if ! command -v gcc &>/dev/null && ! command -v clang &>/dev/null; then
        local_missing+=("build-essential")
    fi
    if ! python3 -c "import venv" &>/dev/null 2>&1; then
        local_missing+=("python3-venv")
    fi
    if ! command -v git &>/dev/null; then
        local_missing+=("git")
    fi

    if [[ ${#local_missing[@]} -gt 0 ]]; then
        info "Installing missing packages: ${local_missing[*]}"
        if   command -v apt &>/dev/null; then
            sudo apt update -qq
            sudo apt install -y -qq "${local_missing[@]}"
        elif command -v yum &>/dev/null; then
            sudo yum install -y "${local_missing[@]}"
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y "${local_missing[@]}"
        else
            warn "Unknown package manager. Please install manually: ${local_missing[*]}"
        fi
        ok "System packages installed"
    else
        ok "System prerequisites satisfied"
    fi
fi
echo

# ---------------------------------------------------------------------------
#  PYTHON VERSION CHECK (≥ 3.11)
# ---------------------------------------------------------------------------
header "4/11  Python ≥ 3.11"

PY_VER=""
PY_CMD=""

# Prefer python3 over python
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver="$($candidate --version 2>&1 | awk '{print $2}')"
        if [[ -n "$ver" ]]; then
            PY_VER="$ver"
            PY_CMD="$candidate"
            break
        fi
    fi
done

if [[ -z "$PY_VER" ]]; then
    fail "Python 3 not found."
    echo "  ${YELLOW}Please install Python 3.11 or later:"
    echo "    macOS : brew install python@3.11"
    echo "    Ubuntu: sudo apt install python3.11 python3.11-venv python3-pip"
    echo "    Fedora: sudo dnf install python3.11 python3.11-pip"
    echo "    Or download from https://www.python.org/downloads/${NC}"
    exit 1
fi

MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
MINOR="$(echo "$PY_VER" | cut -d. -f2)"

if [[ "$MAJOR" -lt 3 ]] || { [[ "$MAJOR" -eq 3 ]] && [[ "$MINOR" -lt 11 ]]; }; then
    fail "Python $PY_VER is too old. Python 3.11+ is required."
    echo "  ${YELLOW}Please upgrade Python and re-run this script.${NC}"
    exit 1
fi

ok "Python $PY_VER ($PY_CMD)"
echo

# ---------------------------------------------------------------------------
#  VIRTUAL ENVIRONMENT
# ---------------------------------------------------------------------------
header "5/11  Virtual environment"

if [[ -d "$VENV_DIR" ]]; then
    ok ".venv already exists at $VENV_DIR"
else
    info "Creating .venv ..."
    "$PY_CMD" -m venv "$VENV_DIR"
    ok ".venv created"
fi

# Verify venv structure (should have bin/pip, bin/python)
if [[ ! -f "$VENV_PYTHON" ]]; then
    warn "Expected $VENV_PYTHON not found — falling back to system python"
    VENV_PYTHON="$PY_CMD"
    VENV_PIP="$PY_CMD -m pip"
else
    # Upgrade pip inside the venv (quiet, no self-downgrade spam)
    "$VENV_PYTHON" -m pip install --quiet --upgrade pip 2>/dev/null || true
fi
echo

# ---------------------------------------------------------------------------
#  INSTALL PACKAGE (pip install -e .)
# ---------------------------------------------------------------------------
header "6/11  Install corpus-kb (editable)"

info "Installing corpus-kb and dependencies ..."
# Use --quiet to reduce noise; tail captures any actual errors or important messages
"$VENV_PIP" install --quiet -e "$PROJECT_ROOT" 2>&1 | tail -5 || {
    rc=$?
    echo "  ${RED}pip install failed (exit $rc). See output above.${NC}"
    exit $rc
}

if [[ -f "$VENV_CORPUS_KB" ]]; then
    ok "corpus-kb installed → $VENV_CORPUS_KB"
else
    warn "corpus-kb entry point not found in venv bin — check pyproject.toml [project.scripts]"
fi
echo

# ---------------------------------------------------------------------------
#  OLLAMA
# ---------------------------------------------------------------------------
header "7/11  Ollama"

EMBEDDING_MODEL="nomic-embed-text"

# --- 7a. Install Ollama if missing ---
if command -v ollama &>/dev/null; then
    OLLAMA_VER="$(ollama --version 2>&1 || true)"
    ok "Ollama found${OLLAMA_VER:+ ($OLLAMA_VER)}"
else
    info "Ollama not found — installing ..."
    case "$OS" in
        macos)
            brew install ollama
            ok "Ollama installed via Homebrew"
            ;;
        linux)
            # Official Ollama install script — one-liner
            curl -fsSL https://ollama.com/install.sh | sh
            ok "Ollama installed via official script"
            ;;
        *)
            warn "Unsupported OS; please install Ollama manually from https://ollama.com"
            ;;
    esac
fi

# --- 7b. Ensure Ollama is running ---
ollama_is_running() {
    curl -sf http://localhost:11434/api/tags > /dev/null 2>&1
}

if ollama_is_running; then
    ok "Ollama server is already running"
else
    info "Starting Ollama server ..."
    ollama serve > /dev/null 2>&1 &
    OLLAMA_PID=$!
    info "Ollama started (PID $OLLAMA_PID)"
fi

# --- 7c. Wait for Ollama health (polling with spinner) ---
info "Waiting for Ollama to be ready ..."
{
    MAX_ATTEMPTS=30  # ~30 seconds
    for ((i=1; i<=MAX_ATTEMPTS; i++)); do
        if ollama_is_running; then
            exit 0
        fi
        sleep 1
    done
    exit 1
} &

SPIN_PID=$!

# Simple spinner
spin_chars='|/-\'
while kill -0 "$SPIN_PID" 2>/dev/null; do
    for ((j=0; j<${#spin_chars}; j++)); do
        printf "\r  ${BLUE}[%s]${NC} polling localhost:11434 ..." "${spin_chars:$j:1}"
        sleep 0.1
    done
done
printf "\r  ${GREEN}[✓]${NC} polling localhost:11434 ...   \n"

wait "$SPIN_PID" && HEALTHY=true || HEALTHY=false

if ! $HEALTHY; then
    fail "Ollama did not become healthy within 30 seconds."
    echo "  ${YELLOW}Check:"
    echo "    - Is port 11434 free?  (lsof -ti:11434)"
    echo "    - Try manually: ollama serve"
    echo "    - View logs: ollama serve 2>&1${NC}"
    exit 1
fi
ok "Ollama is healthy (http://localhost:11434)"

# --- 7d. Pull the embedding model ---
info "Pulling embedding model '${EMBEDDING_MODEL}' ..."
ollama pull "$EMBEDDING_MODEL" 2>&1
ok "Model '${EMBEDDING_MODEL}' pulled"

# --- 7e. Verify embedding works ---
info "Verifying embedding API ..."
EMBED_TEST=$(curl -sf http://localhost:11434/api/generate \
    -d "{\"model\": \"${EMBEDDING_MODEL}\", \"prompt\": \"Corpus-KB verification\", \"options\": {\"embedding_only\": true}}" \
    2>/dev/null || true)

if echo "$EMBED_TEST" | grep -q '"embedding"' || echo "$EMBED_TEST" | grep -q '"done":true'; then
    ok "Embedding model responds correctly"
else
    warn "Embedding verification returned unexpected response."
    warn "Ollama is running and the model is pulled, but the generate API may need attention."
fi
echo

# ---------------------------------------------------------------------------
#  DATA DIRECTORIES
# ---------------------------------------------------------------------------
header "8/11  Data directories"

DATA_DIRS=(
    "data/lancedb"
    "data/graph"
    "data/duckdb"
)

for dir in "${DATA_DIRS[@]}"; do
    abs="$PROJECT_ROOT/$dir"
    if [[ ! -d "$abs" ]]; then
        mkdir -p "$abs"
        ok "Created $dir/"
    else
        ok "$dir/ exists"
    fi
done
echo

# ---------------------------------------------------------------------------
#  SAMPLE FILES (first-run only)
# ---------------------------------------------------------------------------
header "9/11  Sample files"

SAMPLES="$PROJECT_ROOT/samples"
mkdir -p "$SAMPLES"

write_sample() {
    local file="$1" content="$2"
    local path="$SAMPLES/$file"
    if [[ -f "$path" ]]; then
        ok "$file exists (skipped)"
    else
        echo "$content" > "$path"
        ok "Created samples/$file"
    fi
}

write_sample "calculator.py" '\
# Sample Python: Calculator
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

class Calculator:
    def __init__(self):
        self.history = []

    def calculate(self, a, op, b):
        if op == "+":
            result = add(a, b)
        elif op == "-":
            result = subtract(a, b)
        else:
            raise ValueError(f"Unknown operator: {op}")
        self.history.append((a, op, b, result))
        return result
'

write_sample "architecture.md" '\
# Architecture Overview

## Components

The system consists of three main components:

### Storage Layer
Handles vector embeddings (LanceDB), full-text search (DuckDB FTS),
and entity graphs (SQLite/GraphQLite).

### Chunking Engine
Splits documents into semantically coherent chunks
using AST-aware (code) or heading-aware (markdown) strategies.
Detects file types and resolves hierarchy relationships.

### RAG Pipeline
Embeds chunks via Ollama (nomic-embed-text), runs hybrid
search (vector + FTS + RRF), and optionally reranks results.
'

write_sample "readme.txt" '\
Corpus-KB is a local end-to-end RAG system for agentic code editors.
It provides MCP tools for ingesting, searching, and querying code and documentation.

Key features:
- 100% local, no cloud dependencies
- AST-aware code chunking (40+ languages via tree-sitter)
- Hybrid search (vector + full-text + RRF)
- Entity graph with BFS traversal
- SQL queries via DuckDB
- Time-travel versioning
'
echo

# ---------------------------------------------------------------------------
#  UPDATE MCP CONFIG FILES  (replace "corpus-kb" command with absolute venv path)
# ---------------------------------------------------------------------------
header "10/11  MCP editor configs"

# We use a small Python script because JSON + sed is fragile.
# Python is guaranteed available at this point (step 4/11 passed).
"$VENV_PYTHON" - "$MCP_SRC" "$VENV_CORPUS_KB" <<'PYEOF'
"""Rewrite MCP config files to use the absolute venv binary path."""
import json
import os
import sys

src_dir = sys.argv[1]
venv_bin = sys.argv[2]

files = [
    "opencode.json",
    "cursor.json",
    "claude-code.json",
]

updated = 0
skipped = 0
errors = 0

for fname in files:
    fpath = os.path.join(src_dir, fname)
    if not os.path.isfile(fpath):
        continue

    with open(fpath, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  {fname}: invalid JSON ({e}) — skipped")
            errors += 1
            continue

    servers = data.get("mcpServers") or data.get("mcpServers", {})
    mutated = False

    for key, server in servers.items():
        if server.get("command") == "corpus-kb" and server.get("command") != venv_bin:
            server["command"] = venv_bin
            mutated = True
        # Also check nested or alternative keys
        for alt_key in ("bin", "binary", "executable"):
            if alt_key in server and server[alt_key] == "corpus-kb":
                server[alt_key] = venv_bin
                mutated = True

    if not mutated:
        # Check if already absolute path
        already_abs = any(
            server.get("command") == venv_bin
            for server in servers.values()
        )
        if already_abs:
            skipped += 1
            continue

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    print(f"  Updated {fname}: command → {venv_bin}")
    updated += 1

if skipped:
    print(f"  ({skipped} file(s) already up-to-date, skipped)")
if errors:
    print(f"  ({errors} file(s) had errors)")
PYEOF

# --- Copy opencode.json → .opencode/mcp.json ---
OPENCODE_DIR="$PROJECT_ROOT/.opencode"
if [[ ! -d "$OPENCODE_DIR" ]]; then
    mkdir -p "$OPENCODE_DIR"
fi
if [[ -f "$MCP_SRC/opencode.json" ]]; then
    cp "$MCP_SRC/opencode.json" "$OPENCODE_DIR/mcp.json"
    ok "Copied opencode.json → .opencode/mcp.json"
fi

# --- Copy cursor.json → .vscode/mcp.json ---
VSCODE_DIR="$PROJECT_ROOT/.vscode"
if [[ ! -d "$VSCODE_DIR" ]]; then
    mkdir -p "$VSCODE_DIR"
fi
if [[ -f "$MCP_SRC/cursor.json" ]]; then
    cp "$MCP_SRC/cursor.json" "$VSCODE_DIR/mcp.json"
    ok "Copied cursor.json → .vscode/mcp.json"
fi

# --- Claude Code config (~/.claude/mcp.json) ---
CLAUDE_DIR="$HOME/.claude"
if [[ -d "$CLAUDE_DIR" ]] && [[ -f "$MCP_SRC/claude-code.json" ]]; then
    DEST="$CLAUDE_DIR/mcp.json"
    if [[ ! -f "$DEST" ]]; then
        cp "$MCP_SRC/claude-code.json" "$DEST"
        ok "Copied claude-code.json → ~/.claude/mcp.json"
    else
        # Check if it already has our absolute path; if not, merge.
        if grep -q "$VENV_CORPUS_KB" "$DEST" 2>/dev/null; then
            ok "~/.claude/mcp.json already up-to-date"
        else
            warn "~/.claude/mcp.json exists but differs from our config."
            warn "  Manually update the 'command' field to: $VENV_CORPUS_KB"
        fi
    fi
else
    ok "Claude Code config directory not found — skipping (can be set up later)"
fi
echo

# ---------------------------------------------------------------------------
#  SMOKE TEST — RUN DEMO
# ---------------------------------------------------------------------------
header "11/11  Smoke test (scripts/demo.py)"

# Activate venv so demo finds all imports
source "$VENV_BIN/activate"

if [[ ! -f "$SCRIPT_DIR/demo.py" ]]; then
    warn "scripts/demo.py not found — skipping smoke test"
else
    info "Running demo.py ..."
    if "$VENV_PYTHON" "$SCRIPT_DIR/demo.py"; then
        ok "Smoke test passed"
    else
        DEMO_RC=$?
        warn "Demo exited with code $DEMO_RC"
        warn "Check output above — dependencies or Ollama may need attention."
    fi
fi
echo

# ---------------------------------------------------------------------------
#  ELAPSED TIME
# ---------------------------------------------------------------------------
ELAPSED=$SECONDS
MINUTES=$((ELAPSED / 60))
SECS=$((ELAPSED % 60))
if [[ $MINUTES -gt 0 ]]; then
    TIME_STR="${MINUTES}m ${SECS}s"
else
    TIME_STR="${SECS}s"
fi

# ---------------------------------------------------------------------------
#  SETUP COMPLETE — NEXT STEPS
# ---------------------------------------------------------------------------
echo -e "${CYAN}${BOLD}"
echo '╔══════════════════════════════════════════════════════════════╗'
echo '║                 Setup Complete!                             ║'
echo '╚══════════════════════════════════════════════════════════════╝'
echo -e "${NC}"
echo -e "  ${GREEN}✓${NC}  Time elapsed: ${BOLD}${TIME_STR}${NC}"
echo

echo -e "  ${GREEN}${BOLD}Next Steps${NC}"
echo

echo -e "  ${CYAN}┌─ MCP Server (core) ──────────────────────────────┐${NC}"
echo -e "  ${CYAN}│${NC}  ${BOLD}corpus-kb${NC}  —  Start the MCP server (stdio)"
echo -e "  ${CYAN}│${NC}  ${BOLD}corpus-kb --transport sse${NC}  —  Start in SSE mode"
echo -e "  ${CYAN}│${NC}  ${BOLD}$VENV_CORPUS_KB${NC}  —  (absolute path, used by editors)"
echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${NC}"
echo

echo -e "  ${CYAN}┌─ Re-run the demo ────────────────────────────────┐${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC}    source .venv/bin/activate"
echo -e "  ${CYAN}│${NC}    python scripts/demo.py"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${NC}"
echo

echo -e "  ${CYAN}┌─ Editor Integration ─────────────────────────────┐${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC}  ${BOLD}Cline / OpenCode${NC}"
echo -e "  ${CYAN}│${NC}    Config at:  ${BOLD}.opencode/mcp.json${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC}  ${BOLD}VS Code / Cursor / Windsurf${NC}"
echo -e "  ${CYAN}│${NC}    Config at:  ${BOLD}.vscode/mcp.json${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC}  ${BOLD}Claude Code${NC}"
echo -e "  ${CYAN}│${NC}    Config at:  ${BOLD}~/.claude/mcp.json${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${NC}"
echo

echo -e "  ${CYAN}┌─ Useful Commands ────────────────────────────────┐${NC}"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}│${NC}  ${BOLD}ollama list${NC}          —  View downloaded models"
echo -e "  ${CYAN}│${NC}  ${BOLD}ollama pull <model>${NC}  —  Download another model"
echo -e "  ${CYAN}│${NC}  ${BOLD}ollama ps${NC}             —  Show loaded models"
echo -e "  ${CYAN}│${NC}  ${BOLD}ollama serve${NC}          —  Start/restart Ollama"
echo -e "  ${CYAN}│${NC}"
echo -e "  ${CYAN}└──────────────────────────────────────────────────┘${NC}"
echo

echo -e "  ${YELLOW}Tip: Edit ${BOLD}config.yaml${NC}${YELLOW} to change embedding model, chunking, or search.${NC}"
echo -e "  ${YELLOW}     The default model is ${BOLD}nomic-embed-text${NC}${YELLOW} (768d).${NC}"
echo

info "Happy building!"

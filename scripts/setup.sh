#!/usr/bin/env bash
# Corpus-KB Setup Script (Unix/Mac)
# Usage: bash scripts/setup.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
GRAY='\033[0;90m'; NC='\033[0m'

info()  { echo -e "${GRAY}[INFO]${NC} $1"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
header(){ echo; echo -e "${CYAN}━━━ $1 ━━━${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo -e "${CYAN}╔══════════════════════════════════╗${NC}"
echo -e "${CYAN}║       Corpus-KB Setup            ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════╝${NC}"
info "Project root: $PROJECT_ROOT"

# ------------------------------------------------------------------
header "1/8  Python 3.11+"
# ------------------------------------------------------------------
PY_VER=$(python3 --version 2>&1 | awk '{print $2}' || true)
if [ -z "$PY_VER" ]; then
    PY_VER=$(python --version 2>&1 | awk '{print $2}' || true)
    PY_CMD="python"
else
    PY_CMD="python3"
fi

if [ -z "$PY_VER" ]; then
    echo -e "  ${RED}Python not found. Install Python 3.11+ first.${NC}"
    exit 1
fi

MAJOR=$(echo "$PY_VER" | cut -d. -f1)
MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo -e "  ${RED}Python 3.11+ required, found $PY_VER${NC}"
    exit 1
fi
ok "Python $PY_VER ($PY_CMD)"

# ------------------------------------------------------------------
header "2/8  Virtual environment"
# ------------------------------------------------------------------
VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating .venv ..."
    "$PY_CMD" -m venv "$VENV_DIR"
    ok ".venv created"
else
    ok ".venv already exists"
fi

# Determine activate path (Linux/Mac)
if [ -f "$VENV_DIR/bin/activate" ]; then
    ACTIVATE="$VENV_DIR/bin/activate"
    PIP="$VENV_DIR/bin/pip"
    PYTHON="$VENV_DIR/bin/python"
else
    warn "Unexpected venv structure — will use fallback python -m pip"
    PIP="$PY_CMD -m pip"
    PYTHON="$PY_CMD"
fi

# ------------------------------------------------------------------
header "3/8  Install package (pip install -e .)"
# ------------------------------------------------------------------
info "Installing corpus-kb in editable mode ..."
"$PIP" install --quiet -e "$PROJECT_ROOT" 2>&1 | tail -5
ok "Package installed"

# ------------------------------------------------------------------
header "4/8  Ollama"
# ------------------------------------------------------------------
if command -v ollama &>/dev/null; then
    OLLAMA_VER=$(ollama --version 2>&1 || true)
    ok "Ollama found${OLLAMA_VER:+ ($OLLAMA_VER)}"
    info "Pulling nomic-embed-text ..."
    ollama pull nomic-embed-text 2>&1 | tail -1
    ok "nomic-embed-text ready"
else
    warn "Ollama not found. Install from https://ollama.com"
    warn "Then run: ollama pull nomic-embed-text"
fi

# ------------------------------------------------------------------
header "5/8  Data directories"
# ------------------------------------------------------------------
for dir in "data/lancedb" "data/graph"; do
    if [ ! -d "$PROJECT_ROOT/$dir" ]; then
        mkdir -p "$PROJECT_ROOT/$dir"
        ok "Created $dir/"
    else
        ok "$dir/ exists"
    fi
done

# ------------------------------------------------------------------
header "6/8  Sample files"
# ------------------------------------------------------------------
SAMPLES="$PROJECT_ROOT/samples"
mkdir -p "$SAMPLES"

write_sample() {
    local file="$1"
    if [ -f "$SAMPLES/$file" ]; then
        ok "$file exists (skipped)"
    else
        echo "$2" > "$SAMPLES/$file"
        ok "Created $file"
    fi
}

write_sample "calculator.py" "\
# Sample Python: Calculator
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

class Calculator:
    def __init__(self):
        self.history = []

    def calculate(self, a, op, b):
        if op == '+':
            result = add(a, b)
        elif op == '-':
            result = subtract(a, b)
        else:
            raise ValueError(f\"Unknown operator: {op}\")
        self.history.append((a, op, b, result))
        return result
"

write_sample "architecture.md" "\
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
"

write_sample "readme.txt" "\
Corpus-KB is a local end-to-end RAG system for agentic code editors.
It provides MCP tools for ingesting, searching, and querying code and documentation.

Key features:
- 100% local, no cloud dependencies
- AST-aware code chunking (40+ languages via tree-sitter)
- Hybrid search (vector + full-text + RRF)
- Entity graph with BFS traversal
- SQL queries via DuckDB
- Time-travel versioning
"

# ------------------------------------------------------------------
header "7/8  MCP client configs"
# ------------------------------------------------------------------
MCP_SRC="$PROJECT_ROOT/mcp-configs"

# .opencode (opencode CLI / Cline)
OPENCODE_DIR="$PROJECT_ROOT/.opencode"
if [ ! -d "$OPENCODE_DIR" ]; then
    mkdir -p "$OPENCODE_DIR"
    ok "Created .opencode/"
fi
if [ -f "$MCP_SRC/opencode.json" ]; then
    DEST="$OPENCODE_DIR/mcp.json"
    cp "$MCP_SRC/opencode.json" "$DEST"
    ok "Copied mcp.json → .opencode/"
fi

# .vscode (VS Code / Cursor / Windsurf)
VSCODE_DIR="$PROJECT_ROOT/.vscode"
if [ ! -d "$VSCODE_DIR" ]; then
    mkdir -p "$VSCODE_DIR"
    ok "Created .vscode/"
fi
if [ -f "$MCP_SRC/cursor.json" ]; then
    DEST="$VSCODE_DIR/mcp.json"
    cp "$MCP_SRC/cursor.json" "$DEST"
    ok "Copied cursor.json → .vscode/mcp.json"
fi

# claude-code config
CLAUDE_DIR="$HOME/.claude"
if [ -d "$CLAUDE_DIR" ] && [ -f "$MCP_SRC/claude-code.json" ]; then
    DEST="$CLAUDE_DIR/mcp.json"
    if [ ! -f "$DEST" ]; then
        cp "$MCP_SRC/claude-code.json" "$DEST"
        ok "Copied claude-code.json → ~/.claude/mcp.json"
    else
        ok "~/.claude/mcp.json exists (skipped)"
    fi
fi

# ------------------------------------------------------------------
header "8/8  Smoke test (scripts/demo.py)"
# ------------------------------------------------------------------
. "$ACTIVATE"
info "Running demo.py ..."
if "$PYTHON" "$SCRIPT_DIR/demo.py"; then
    ok "Smoke test passed"
else
    warn "Demo exited with code $?"
    info "Check output above — dependencies or Ollama may need attention."
fi

# ------------------------------------------------------------------
header "✔ Setup complete!"
# ------------------------------------------------------------------
echo -e "  ${GREEN}Next steps:${NC}"
echo -e "    ${CYAN}python scripts/demo.py${NC}     — Re-run the demo anytime"
echo -e "    ${CYAN}corpus-kb${NC}                    — Start the MCP server (stdio)"
echo -e "    ${CYAN}corpus-kb --transport sse${NC}   — Start in SSE mode"
echo
info "Happy building!"

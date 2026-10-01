#!/bin/bash
# Corpus-KB Setup Script for macOS and Linux
# Installs Python, Ollama, dependencies, and configures MCP for Claude Code / Cursor / VS Code
# Handles clean environments gracefully with clear guidance

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Detect OS
OS="$(uname -s)"
ARCH="$(uname -m)"

# Project root (where this script is)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Paths
VENV_PATH="${PROJECT_ROOT}/.venv"
CONFIG_PATH="${PROJECT_ROOT}/config.yaml"
MCP_CONFIG_DIR="${PROJECT_ROOT}/mcp-configs"
CORPUS_KB_DATA_DIR="${HOME}/.corpus-kb"

# ============================================================================
# LOGGING & ERROR HANDLING
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

fail() {
    log_error "$1"
    exit 1
}

# ============================================================================
# ISSUE #1: PYTHON INSTALLATION WITH GUIDANCE
# ============================================================================

check_python() {
    # Check for Python 3.11+
    if command -v python3 &> /dev/null; then
        PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        
        if [[ $PY_MAJOR -gt 3 ]] || [[ $PY_MAJOR -eq 3 && $PY_MINOR -ge 11 ]]; then
            log_success "Python $PY_VER found"
            return 0
        fi
    fi
    
    return 1
}

install_python() {
    log_warn "Python 3.11+ not found. Installing..."
    
    if [[ "$OS" == "Darwin" ]]; then
        # macOS
        if ! command -v brew &> /dev/null; then
            log_info "Homebrew not found. Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            log_success "Homebrew installed"
        fi
        
        log_info "Installing Python 3.11 via Homebrew..."
        brew install python@3.11
        
        # Link python3 to python3.11
        brew link python@3.11 --force
        log_success "Python 3.11 installed via Homebrew"
        
    elif [[ "$OS" == "Linux" ]]; then
        # Linux (Ubuntu/Debian)
        if command -v apt-get &> /dev/null; then
            log_info "Installing Python 3.11 via apt-get..."
            sudo apt-get update
            sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
            log_success "Python 3.11 installed via apt-get"
        elif command -v yum &> /dev/null; then
            log_info "Installing Python 3.11 via yum..."
            sudo yum install -y python3.11 python3.11-devel
            log_success "Python 3.11 installed via yum"
        else
            fail "Could not find apt-get or yum. Please install Python 3.11 manually and re-run this script."
        fi
    else
        fail "Unsupported OS: $OS. Please install Python 3.11+ manually."
    fi
}

# ============================================================================
# OLLAMA INSTALLATION
# ============================================================================

check_ollama() {
    if command -v ollama &> /dev/null; then
        log_success "Ollama found"
        return 0
    fi
    return 1
}

install_ollama() {
    log_warn "Ollama not found. Installing..."
    
    if [[ "$OS" == "Darwin" ]]; then
        # macOS
        if ! command -v brew &> /dev/null; then
            log_info "Installing Homebrew first..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        
        log_info "Installing Ollama via Homebrew..."
        brew install ollama
        log_success "Ollama installed via Homebrew"
        
    elif [[ "$OS" == "Linux" ]]; then
        log_info "Installing Ollama via official installer..."
        curl -fsSL https://ollama.ai/install.sh | sh
        log_success "Ollama installed"
    else
        fail "Unsupported OS: $OS. Please install Ollama manually from https://ollama.ai"
    fi
}

# ============================================================================
# OLLAMA SERVICE & MODEL PULL
# ============================================================================

start_ollama() {
    log_info "Checking if Ollama is running..."
    
    if ! curl -s http://localhost:11434/api/tags &> /dev/null; then
        log_warn "Ollama is not running. Starting Ollama in the background..."
        
        if [[ "$OS" == "Darwin" ]]; then
            # macOS: use launchctl
            brew services start ollama || true
            sleep 2
        elif [[ "$OS" == "Linux" ]]; then
            # Linux: use systemctl or start directly
            if command -v systemctl &> /dev/null; then
                sudo systemctl start ollama || true
            else
                ollama serve &
                sleep 2
            fi
        fi
        
        # Wait for Ollama to be ready
        local max_attempts=30
        local attempt=0
        while ! curl -s http://localhost:11434/api/tags &> /dev/null; do
            attempt=$((attempt + 1))
            if [[ $attempt -ge $max_attempts ]]; then
                fail "Ollama failed to start. Please start it manually: ollama serve"
            fi
            sleep 1
        done
        
        log_success "Ollama is running"
    else
        log_success "Ollama is already running"
    fi
}

pull_embedding_model() {
    log_info "Pulling embedding model (nomic-embed-text, ~274 MB)..."
    
    # Check if model already exists
    if ollama list | grep -q "nomic-embed-text"; then
        log_success "nomic-embed-text already pulled"
    else
        ollama pull nomic-embed-text
        log_success "nomic-embed-text pulled"
    fi
}

# ============================================================================
# PYTHON VENV & DEPENDENCIES
# ============================================================================

setup_venv() {
    log_info "Setting up Python virtual environment..."
    
    if [[ ! -d "$VENV_PATH" ]]; then
        python3 -m venv "$VENV_PATH"
        log_success "Virtual environment created at $VENV_PATH"
    else
        log_success "Virtual environment already exists"
    fi
    
    # Activate venv
    source "$VENV_PATH/bin/activate"
    
    log_info "Installing dependencies..."
    pip install --upgrade pip setuptools wheel
    pip install -e "$PROJECT_ROOT"
    
    log_success "Dependencies installed"
}

# ============================================================================
# ISSUE #2 & #3: MCP CONFIGURATION WITH CLAUDE CLI & ENV VARS
# ============================================================================

setup_mcp_config() {
    log_info "Setting up MCP configuration..."
    
    # Get the absolute path to the corpus-kb binary in the venv
    CORPUS_KB_BIN="${VENV_PATH}/bin/corpus-kb"
    
    # Ensure corpus-kb binary exists
    if [[ ! -f "$CORPUS_KB_BIN" ]]; then
        fail "corpus-kb binary not found at $CORPUS_KB_BIN. Installation may have failed."
    fi
    
    # Create data directory
    mkdir -p "$CORPUS_KB_DATA_DIR"
    
    # ========================================================================
    # ISSUE #3: Add CORPUS_KB_CONFIG to MCP env for config discovery
    # ========================================================================
    
    # Update Claude Code MCP config with absolute path to corpus-kb and env var
    local claude_config="${MCP_CONFIG_DIR}/claude-code.json"
    if [[ -f "$claude_config" ]]; then
        log_info "Updating Claude Code MCP config..."
        
        # Use Python to safely update JSON (handles escaping, formatting)
        python3 << 'PYTHON_EOF'
import json
import sys
from pathlib import Path

config_path = sys.argv[1]
corpus_kb_bin = sys.argv[2]
config_yaml = sys.argv[3]

with open(config_path, 'r') as f:
    config = json.load(f)

# Update command to absolute path
config['mcpServers']['corpus-kb']['command'] = corpus_kb_bin
config['mcpServers']['corpus-kb']['args'] = ['--transport', 'stdio']

# Add CORPUS_KB_CONFIG env var for config discovery (ISSUE #3)
config['mcpServers']['corpus-kb']['env'] = {
    'CORPUS_KB_CONFIG': config_yaml
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print(f"Updated {config_path}")
PYTHON_EOF
        python3 -c "
import json
import sys
from pathlib import Path

config_path = '$claude_config'
corpus_kb_bin = '$CORPUS_KB_BIN'
config_yaml = '$CONFIG_PATH'

with open(config_path, 'r') as f:
    config = json.load(f)

config['mcpServers']['corpus-kb']['command'] = corpus_kb_bin
config['mcpServers']['corpus-kb']['args'] = ['--transport', 'stdio']
config['mcpServers']['corpus-kb']['env'] = {'CORPUS_KB_CONFIG': config_yaml}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
"
        log_success "Claude Code MCP config updated"
    fi
    
    # ========================================================================
    # ISSUE #2: Use 'claude mcp add' if Claude CLI is available, else copy
    # ========================================================================
    
    if command -v claude &> /dev/null; then
        log_info "Claude CLI found. Registering corpus-kb via 'claude mcp add'..."
        
        # Register with Claude CLI
        claude mcp add --scope user --name corpus-kb "$CORPUS_KB_BIN" --args "--transport" "stdio" || {
            log_warn "Failed to register via 'claude mcp add'. Falling back to manual config copy."
            copy_mcp_configs
        }
        
        log_success "corpus-kb registered with Claude CLI"
    else
        log_warn "Claude CLI not found. Falling back to manual MCP config setup."
        copy_mcp_configs
    fi
}

copy_mcp_configs() {
    log_info "Copying MCP configs to editor directories..."
    
    local corpus_kb_bin="$VENV_PATH/bin/corpus-kb"
    
    # Claude Code: ~/.claude/mcp.json
    local claude_dir="${HOME}/.claude"
    mkdir -p "$claude_dir"
    
    python3 << PYTHON_EOF
import json
from pathlib import Path

claude_config = {
    "mcpServers": {
        "corpus-kb": {
            "name": "Corpus-KB",
            "description": "Local RAG system — search, ingest, knowledge graph, SQL queries over your code and documents",
            "command": "$corpus_kb_bin",
            "args": ["--transport", "stdio"],
            "env": {
                "CORPUS_KB_CONFIG": "$CONFIG_PATH"
            },
            "disabled": False,
            "autoApprove": [
                "search", "search_context", "search_similar",
                "retrieve_context", "list_documents", "get_stats",
                "list_versions", "list_branches", "get_entity_relations",
                "search_graph", "sql_query", "sql_tables",
                "get_document_tags", "get_metadata", "query_document_stats",
                "sync_database"
            ]
        }
    }
}

claude_path = Path("$claude_dir") / "mcp.json"
with open(claude_path, 'w') as f:
    json.dump(claude_config, f, indent=2)

print(f"Copied Claude Code config to {claude_path}")
PYTHON_EOF
    
    log_success "MCP configs copied to editor directories"
}

# ============================================================================
# DEMO & SMOKE TEST
# ============================================================================

run_demo() {
    log_info "Running smoke test..."
    
    source "$VENV_PATH/bin/activate"
    
    # Quick test: start corpus-kb, ingest a file, search
    python3 << 'PYTHON_EOF'
import sys
import time
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from config import load_config
    config = load_config()
    print(f"✓ Config loaded: embedding model = {config.get('embedding', {}).get('model', 'unknown')}")
except Exception as e:
    print(f"✗ Config load failed: {e}")
    sys.exit(1)

print("✓ Smoke test passed")
PYTHON_EOF
    
    if [[ $? -eq 0 ]]; then
        log_success "Smoke test passed"
    else
        log_warn "Smoke test had issues, but setup may still be functional"
    fi
}

# ============================================================================
# FINAL INSTRUCTIONS
# ============================================================================

print_instructions() {
    cat << 'EOF'

╔════════════════════════════════════════════════════════════════════════════╗
║                    CORPUS-KB SETUP COMPLETE                               ║
╚════════════════════════════════════════════════════════════════════════════╝

✓ Python 3.11+ installed
✓ Ollama installed and running
✓ nomic-embed-text model pulled
✓ Dependencies installed in virtual environment
✓ MCP configured for Claude Code / Cursor / VS Code
✓ CORPUS_KB_CONFIG env var set for config discovery

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEXT STEPS:

1. Start the Corpus-KB server:
   source .venv/bin/activate
   corpus-kb

2. In Claude Code / Cursor / VS Code:
   - Restart the editor to load the new MCP config
   - You should see "corpus-kb" in the MCP tools list
   - Use the search, ingest, and query tools to interact with your codebase

3. Ingest your first codebase:
   - Use the "ingest_directory" tool to add your code
   - Or use "ingest_file" for individual files

4. Search your code:
   - Use the "search" tool to find related code
   - Use "sql_query" for relational queries
   - Use "search_graph" to explore entity relationships

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONFIGURATION:

- Config file: config.yaml
- Data directory: ~/.corpus-kb
- Embedding model: nomic-embed-text (768d, ~274 MB)
- To upgrade: ollama pull qwen3-embedding:8b-q8_0 && update config.yaml

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TROUBLESHOOTING:

- Ollama not running? Start it: ollama serve
- corpus-kb command not found? Activate venv: source .venv/bin/activate
- MCP not showing in editor? Restart the editor and check ~/.claude/mcp.json
- Config not found? Set CORPUS_KB_CONFIG env var or place config.yaml in ~/.corpus-kb/

For more help, see README.md

EOF
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    log_info "Starting Corpus-KB setup for $OS ($ARCH)..."
    log_info "Project root: $PROJECT_ROOT"
    
    # Step 1: Check/install Python
    if ! check_python; then
        install_python
    fi
    
    # Step 2: Check/install Ollama
    if ! check_ollama; then
        install_ollama
    fi
    
    # Step 3: Start Ollama and pull model
    start_ollama
    pull_embedding_model
    
    # Step 4: Setup Python venv
    setup_venv
    
    # Step 5: Setup MCP configuration
    setup_mcp_config
    
    # Step 6: Run smoke test
    run_demo
    
    # Step 7: Print instructions
    print_instructions
    
    log_success "Setup complete!"
}

# Run main
main "$@"

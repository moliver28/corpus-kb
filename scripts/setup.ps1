# Corpus-KB Setup Script (Windows PowerShell)
# Run: powershell -ExecutionPolicy Bypass -File scripts/setup.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host "=== Corpus-KB Setup ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray

# 1. Create virtual environment
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "`n[1/4] Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv $VenvDir
    Write-Host "  Done." -ForegroundColor Green
} else {
    Write-Host "`n[1/4] Virtual environment already exists." -ForegroundColor Gray
}

# 2. Activate and install dependencies
Write-Host "`n[2/4] Installing dependencies..." -ForegroundColor Yellow
$Pip = Join-Path $VenvDir "Scripts\pip"
if (-not (Test-Path $Pip)) {
    # Fallback to system pip if venv pip doesn't exist yet
    $Pip = "python -m pip"
}
& cmd /c "$Pip install -e `"$ProjectRoot`" 2>&1" | Out-Null
Write-Host "  Done." -ForegroundColor Green

# 3. Install Ollama
Write-Host "`n[3/4] Checking Ollama..." -ForegroundColor Yellow
try {
    $ollamaVersion = & ollama --version 2>&1
    Write-Host "  Ollama found: $ollamaVersion" -ForegroundColor Green

    # Pull embedding model
    Write-Host "  Pulling nomic-embed-text..." -ForegroundColor Gray
    & ollama pull nomic-embed-text 2>&1 | Out-Null
    Write-Host "  Model ready." -ForegroundColor Green
} catch {
    Write-Host "  Ollama not found. Install from https://ollama.ai/download" -ForegroundColor Red
    Write-Host "  Then run: ollama pull nomic-embed-text" -ForegroundColor Yellow
}

# 4. Create data directories
Write-Host "`n[4/4] Creating data directories..." -ForegroundColor Yellow
$DataDir = Join-Path $ProjectRoot "data"
@("lancedb", "graph") | ForEach-Object {
    $dir = Join-Path $DataDir $_
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Gray
    }
}
Write-Host "  Done." -ForegroundColor Green

# 5. Sample data
$SampleDir = Join-Path $ProjectRoot "samples"
if (-not (Test-Path $SampleDir)) {
    New-Item -ItemType Directory -Path $SampleDir -Force | Out-Null
    @"
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
            raise ValueError(f"Unknown operator: {op}")
        self.history.append((a, op, b, result))
        return result
"@ | Set-Content (Join-Path $SampleDir "calculator.py") -Encoding utf8

    @"
# Sample Markdown: Notes
# Architecture Overview

## Components

The system consists of three main components:

### Storage Layer
Handles vector embeddings, full-text search, and SQL queries.

### Chunking Engine
Splits documents into semantically coherent chunks
using AST-aware (code) or heading-aware (markdown) strategies.

### RAG Pipeline
Embeds chunks, runs hybrid search, and reranks results.
"@ | Set-Content (Join-Path $SampleDir "architecture.md") -Encoding utf8

    @"
# Sample Text: Notes
Corpus-KB is a local end-to-end RAG system for agentic code editors.
It provides MCP tools for ingesting, searching, and querying code and documentation.

Key features:
- 100% local, no cloud dependencies
- AST-aware code chunking (40+ languages)
- Hybrid search (vector + full-text + RRF)
- Entity graph with BFS traversal
- SQL queries via DuckDB
- Time-travel versioning
"@ | Set-Content (Join-Path $SampleDir "readme.txt") -Encoding utf8

    Write-Host "`nSample files created in: $SampleDir" -ForegroundColor Green
}

Write-Host "`n=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "Run the demo:" -ForegroundColor White
Write-Host "  python scripts/demo.py" -ForegroundColor Yellow
Write-Host "Or start the server:" -ForegroundColor White
Write-Host "  corpus-kb" -ForegroundColor Yellow

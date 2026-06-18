#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Bootstraps the corpus-kb local RAG system on Windows.
.DESCRIPTION
    Idempotent setup script for Windows that:
      - Checks prerequisites (Python 3.11+, Ollama, internet)
      - Creates a Python virtual environment
      - Installs corpus-kb in editable development mode
      - Installs Ollama if missing and pulls the nomic-embed-text embedding model
      - Creates data directories and sample files
      - Rewrites MCP editor configs to use absolute venv binary paths
      - Runs the demo smoke test
      - Prints next steps for all AI code editors

    Safe to re-run — checks existing state before acting.
.NOTES
    Author   : Corpus-KB Team
    Version  : 0.2.0
    Requires : Windows 10/11, PowerShell 5.1+
    Usage    : powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>

# ---------------------------------------------------------------------------
#  STRICT MODE
# ---------------------------------------------------------------------------
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
#  CONSTANTS
# ---------------------------------------------------------------------------
# Colours
$C_CYAN    = 'Cyan'
$C_GREEN   = 'Green'
$C_YELLOW  = 'Yellow'
$C_RED     = 'Red'
$C_GRAY    = 'Gray'
$C_WHITE   = 'White'
$C_BLUE    = 'Blue'
$C_MAGENTA = 'Magenta'

$EMBEDDING_MODEL        = "nomic-embed-text"
$OLLAMA_HEALTH_URL      = "http://localhost:11434/api/tags"
$OLLAMA_GENERATE_URL    = "http://localhost:11434/api/generate"
$OLLAMA_POLL_TIMEOUT_SEC = 60
$OLLAMA_POLL_INTERVAL_SEC = 2
$PYTHON_MIN_MAJOR       = 3
$PYTHON_MIN_MINOR       = 11
$PYTHON_WINGET_ID       = "Python.Python.3.11"
$PYTHON_INSTALLER_URL   = "https://www.python.org/ftp/python/3.11.11/python-3.11.11-amd64.exe"
$PYTHON_INSTALLER_URL_32= "https://www.python.org/ftp/python/3.11.11/python-3.11.11.exe"

# ---------------------------------------------------------------------------
#  LOGGING HELPERS
# ---------------------------------------------------------------------------
function Write-Info  { Write-Host "   [INFO] $args" -ForegroundColor $C_GRAY }
function Write-Ok    { Write-Host "   $(CheckMark) $args" -ForegroundColor $C_GREEN }
function Write-Warn  { Write-Host "   $(WarnMark) $args" -ForegroundColor $C_YELLOW }
function Write-Fail  { Write-Host "   $(CrossMark) $args" -ForegroundColor $C_RED }
function Write-Header {
    param([string]$Title)
    Write-Host "`n--- $Title ---" -ForegroundColor $C_CYAN
}
function Write-Sub   { Write-Host "    -> $args" -ForegroundColor $C_BLUE }
function CheckMark   { return [char]0x221A }   # ✓
function CrossMark   { return [char]0x00D7 }   # ×
function WarnMark    { return [char]0x26A0 }   # ⚠

# ---------------------------------------------------------------------------
#  PATH HELPERS
# ---------------------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $PSCommandPath
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvDir     = Join-Path $ProjectRoot ".venv"
$VenvPython  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip     = Join-Path $VenvDir "Scripts\pip.exe"
$VenvCorpus  = Join-Path $VenvDir "Scripts\corpus-kb.exe"
$McpSrcDir   = Join-Path $ProjectRoot "mcp-configs"
$DemoPy      = Join-Path $ProjectRoot "scripts\demo.py"

# ---------------------------------------------------------------------------
#  BANNER
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════════════════════╗" -ForegroundColor $C_CYAN
Write-Host "║                   Corpus-KB  Setup                                        ║" -ForegroundColor $C_CYAN
Write-Host "║  Local RAG system - MCP tools for AI code editors                         ║" -ForegroundColor $C_CYAN
Write-Host "╚══════════════════════════════════════════════════════════════════════════════╝" -ForegroundColor $C_CYAN
Write-Host ""
Write-Info "Project root : $ProjectRoot"
Write-Info "Script       : $PSCommandPath"
Write-Info "Platform     : Windows $([Environment]::OSVersion.Version)"
Write-Host ""

# ---------------------------------------------------------------------------
#  TIMER START
# ---------------------------------------------------------------------------
$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

# ---------------------------------------------------------------------------
#  HELPER FUNCTIONS
# ---------------------------------------------------------------------------
function Refresh-EnvironmentPath {
    <#
    .SYNOPSIS
        Reloads PATH from registry into the current process.
    #>
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath;$env:Path"

    # Deduplicate without removing intentional duplicates
    $paths = $env:Path -split ';' | Where-Object { $_ -ne '' }
    $seen = @{}
    $unique = @()
    foreach ($p in $paths) {
        $normalized = $p.TrimEnd('\').ToLowerInvariant()
        if (-not $seen.ContainsKey($normalized)) {
            $seen[$normalized] = $true
            $unique += $p
        }
    }
    $env:Path = $unique -join ';'
}

function Test-Internet {
    <#
    .SYNOPSIS
        Checks internet connectivity by probing multiple endpoints.
    #>
    $endpoints = @(
        "https://google.com",
        "https://github.com",
        "https://ollama.com",
        "https://pypi.org"
    )
    foreach ($url in $endpoints) {
        try {
            $null = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 5 -UseBasicParsing
            return $true
        } catch {
            # Try next endpoint
        }
    }
    return $false
}

function Get-PythonVersionInfo {
    <#
    .SYNOPSIS
        Parses the version string from a Python executable.
    #>
    param([string]$Path, [string[]]$ArgumentList = @())
    try {
        $verStr = & $Path $ArgumentList --version 2>&1
        if ($verStr -match 'Python (\d+)\.(\d+)\.(\d+)') {
            return @{
                Major   = [int]$Matches[1]
                Minor   = [int]$Matches[2]
                Patch   = [int]$Matches[3]
                Version = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
                Cmd     = $Path
            }
        }
    } catch {
        # Not a valid Python executable
    }
    return $null
}

function Test-PythonSuitable {
    <#
    .SYNOPSIS
        Returns true if the given Python info meets minimum version requirements.
    #>
    param($PythonInfo)
    return $PythonInfo -and `
           $PythonInfo.Major -eq $PYTHON_MIN_MAJOR -and `
           $PythonInfo.Minor -ge $PYTHON_MIN_MINOR
}

function Find-PythonOnPath {
    <#
    .SYNOPSIS
        Searches PATH for a suitable Python 3.11+ executable.
    #>
    $candidates = @("python", "python3", "py")
    foreach ($candidate in $candidates) {
        $info = Get-PythonVersionInfo $candidate
        if (Test-PythonSuitable $info) {
            return $info
        }
    }

    # Try py launcher with explicit version requests
    try {
        $pyList = & py --list 2>&1 | Out-String
        $matches = [regex]::Matches($pyList, '-(\d+)\.(\d+)')
        foreach ($m in $matches) {
            $major = [int]$m.Groups[1].Value
            $minor = [int]$m.Groups[2].Value
            if ($major -eq $PYTHON_MIN_MAJOR -and $minor -ge $PYTHON_MIN_MINOR) {
                $info = Get-PythonVersionInfo "py" @("-3.$minor")
                if (Test-PythonSuitable $info) {
                    return $info
                }
            }
        }
    } catch {
        # py launcher not available
    }

    return $null
}

function Install-PythonViaWinget {
    <#
    .SYNOPSIS
        Attempts to install Python via Windows Package Manager.
    #>
    try {
        Write-Sub "Trying winget (Windows Package Manager) ..."
        $result = & winget install $PYTHON_WINGET_ID --accept-package-agreements --accept-source-agreements 2>&1
        Write-Host $result
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Python 3.11 installed via winget"
            return $true
        } else {
            Write-Warn "winget install returned exit code $LASTEXITCODE"
            return $false
        }
    } catch {
        Write-Warn "winget install failed: $_"
        return $false
    }
}

function Install-PythonViaDownload {
    <#
    .SYNOPSIS
        Downloads and silently installs Python from python.org.
    #>
    try {
        $is64Bit = [Environment]::Is64BitOperatingSystem
        $url = if ($is64Bit) { $PYTHON_INSTALLER_URL } else { $PYTHON_INSTALLER_URL_32 }
        $installerPath = Join-Path $env:TEMP "python-3.11.11-amd64.exe"

        Write-Sub "Downloading Python 3.11 installer ..."
        Invoke-WebRequest -Uri $url -OutFile $installerPath -UseBasicParsing

        Write-Sub "Running installer (silent, all users, add to PATH) ..."
        $installArgs = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"
        $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -NoNewWindow -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Ok "Python 3.11 installed from python.org"
            return $true
        } else {
            Write-Warn "Python installer exit code: $($proc.ExitCode)"
            return $false
        }
    } catch {
        Write-Warn "Download/install from python.org failed: $_"
        return $false
    }
}

function Ensure-OllamaServiceRunning {
    <#
    .SYNOPSIS
        Ensures the Ollama service or process is running.
    #>
    # First check if already responding
    try {
        $null = Invoke-WebRequest -Uri $OLLAMA_HEALTH_URL -Method GET -TimeoutSec 3 -UseBasicParsing
        return $true
    } catch {
        # Not responding — try to start it
    }

    # Try Windows service
    try {
        $svc = Get-Service -Name "Ollama" -ErrorAction Stop
        if ($svc.Status -ne 'Running') {
            Write-Info "Starting Ollama Windows service ..."
            Start-Service -Name "Ollama" -ErrorAction Stop
        }
        return $true
    } catch {
        Write-Info "Ollama service not available, attempting background process ..."
    }

    # Try starting ollama serve as background process
    try {
        $ollamaPath = (Get-Command "ollama.exe" -ErrorAction Stop).Source
        $proc = Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden -PassThru
        Write-Info "Started Ollama as background process (PID: $($proc.Id))"
        return $true
    } catch {
        Write-Warn "Could not start Ollama automatically: $_"
        return $false
    }
}

# ---------------------------------------------------------------------------
#  1/12  WINDOWS OS DETECTION
# ---------------------------------------------------------------------------
Write-Header "1/12  Platform detection"

if ($IsLinux -or $IsMacOS) {
    Write-Fail "This script is designed for Windows only."
    Write-Warn "Please use scripts/setup.sh on macOS / Linux."
    exit 1
}

$PSCore = ($PSVersionTable.PSEdition -eq 'Core')
if ($PSCore) {
    Write-Ok "PowerShell $($PSVersionTable.PSVersion) Core on Windows"
} else {
    Write-Ok "Windows PowerShell $($PSVersionTable.PSVersion)"
}

$Is64Bit = [Environment]::Is64BitOperatingSystem
if (-not $Is64Bit) {
    Write-Warn "32-bit Windows detected. Some components may not work correctly."
}
Write-Ok "Architecture: $(if ($Is64Bit) { 'x64' } else { 'x86' })"

# ---------------------------------------------------------------------------
#  2/12  ADMINISTRATOR CHECK
# ---------------------------------------------------------------------------
Write-Header "2/12  Administrator privileges"

$IsAdmin = ([Security.Principal.WindowsPrincipal]`
    [Security.Principal.WindowsIdentity]::GetCurrent()`
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($IsAdmin) {
    Write-Ok "Running as Administrator"
} else {
    Write-Warn "Not running as Administrator. Some operations may fail:"
    Write-Warn "  - Installing Python via winget (may need elevation)"
    Write-Warn "  - Installing Ollama via winget (may need elevation)"
    Write-Warn "If failures occur, re-run from an elevated PowerShell prompt."
}

# ---------------------------------------------------------------------------
#  3/12  INTERNET CONNECTIVITY CHECK
# ---------------------------------------------------------------------------
Write-Header "3/12  Internet connectivity"

if (Test-Internet) {
    Write-Ok "Internet reachable"
} else {
    Write-Fail "No internet connection detected."
    Write-Warn "This script requires internet to:"
    Write-Warn "  - Install/update Python"
    Write-Warn "  - Install Python dependencies from PyPI"
    Write-Warn "  - Download Ollama and embedding models"
    Write-Warn "Please check your connection and re-run."
    exit 1
}

# ---------------------------------------------------------------------------
#  4/12  LONG PATHS SUPPORT (Windows 10/11)
# ---------------------------------------------------------------------------
Write-Header "4/12  Long paths support"

$LongPathsKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
$LongPathsName = 'LongPathsEnabled'
try {
    $current = Get-ItemProperty -Path $LongPathsKey -Name $LongPathsName -ErrorAction Stop
    if ($current.LongPathsEnabled -ne 1) {
        if ($IsAdmin) {
            Set-ItemProperty -Path $LongPathsKey -Name $LongPathsName -Value 1
            Write-Ok "Enabled LongPathsEnabled (path length > 260 chars)"
        } else {
            Write-Warn "LongPathsEnabled is disabled. Run as Admin to enable it."
        }
    } else {
        Write-Ok "LongPathsEnabled is already active"
    }
} catch {
    Write-Warn "Could not check LongPathsEnabled registry setting."
}

# ---------------------------------------------------------------------------
#  5/12  PYTHON 3.11+ CHECK / INSTALL
# ---------------------------------------------------------------------------
Write-Header "5/12  Python 3.11+"

$Script:PythonCmd = $null
$foundPython = Find-PythonOnPath

if ($foundPython) {
    Write-Ok "Python $($foundPython.Version) found: $($foundPython.Cmd)"
} else {
    Write-Warn "Python 3.11+ not found on PATH."
    Write-Info "Attempting to install Python 3.11 ..."

    $pythonInstalled = $false

    # Method 1: winget
    Write-Info "Method 1: Windows Package Manager (winget) ..."
    $pythonInstalled = Install-PythonViaWinget

    # Method 2: Direct download from python.org
    if (-not $pythonInstalled) {
        Write-Info "Method 2: Direct download from python.org ..."
        $pythonInstalled = Install-PythonViaDownload
    }

    if (-not $pythonInstalled) {
        Write-Fail "Could not install Python automatically."
        Write-Warn "Please install Python 3.11+ manually from:"
        Write-Warn "  https://www.python.org/downloads/"
        Write-Warn "Ensure 'Add Python to PATH' is checked during installation."
        Write-Warn "Then re-run this script."
        exit 1
    }

    # Refresh PATH so the new Python is found
    Refresh-EnvironmentPath
    $foundPython = Find-PythonOnPath

    if (-not $foundPython) {
        Write-Fail "Python was installed but still not found on PATH."
        Write-Warn "Please close and re-open your terminal, then re-run this script."
        exit 1
    }

    Write-Ok "Python $($foundPython.Version) found after install: $($foundPython.Cmd)"
}

# Verify venv module is available
try {
    & $foundPython.Cmd -m venv -h *>$null
    Write-Ok "Python venv module is available"
} catch {
    Write-Fail "Python venv module is not available."
    Write-Warn "On Windows, this usually means Python was installed without the 'pip and venv' option."
    Write-Warn "Please re-install Python and ensure 'pip' is included."
    exit 1
}

$Script:PythonCmd = $foundPython.Cmd

# ---------------------------------------------------------------------------
#  6/12  VIRTUAL ENVIRONMENT + PIP INSTALL
# ---------------------------------------------------------------------------
Write-Header "6/12  Virtual environment"

if (Test-Path $VenvDir) {
    Write-Ok ".venv already exists at $VenvDir"
} else {
    Write-Info "Creating virtual environment ..."
    try {
        & $Script:PythonCmd -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed (exit $LASTEXITCODE)" }
        Write-Ok ".venv created at $VenvDir"
    } catch {
        Write-Fail "Failed to create virtual environment: $_"
        exit 1
    }
}

# Verify venv Python exists
if (-not (Test-Path $VenvPython)) {
    Write-Warn "$VenvPython not found — recreating venv ..."
    Remove-Item -Path $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
    try {
        & $Script:PythonCmd -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "venv re-creation failed" }
        Write-Ok ".venv re-created"
    } catch {
        Write-Fail "Could not create venv. Falling back to system Python."
        $Script:VenvPythonAlt = $Script:PythonCmd
        $Script:VenvPipAlt = "pip"
    }
}

# Use correct venv paths (handles fallback)
if (-not (Test-Path $VenvPython)) {
    $VenvPythonActual = $Script:PythonCmd
    $VenvPipActual = "pip"
} else {
    $VenvPythonActual = $VenvPython
    $VenvPipActual = $VenvPip
}

# Upgrade pip
Write-Info "Upgrading pip ..."
try {
    & $VenvPythonActual -m pip install --upgrade pip --quiet 2>&1 | Out-Null
    Write-Ok "pip upgraded to latest"
} catch {
    Write-Warn "pip upgrade failed (non-critical): $_"
}

# ---------------------------------------------------------------------------
#  7/12  INSTALL CORPUS-KB (pip install -e .)
# ---------------------------------------------------------------------------
Write-Header "7/12  Install corpus-kb (editable)"

Write-Info "Installing corpus-kb and dependencies (this may take a while) ..."

# Check for MSVC build tools (needed for tree-sitter native extensions)
$hasMsvcBuildTools = $false
try {
    $clTest = & cl.exe 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { $hasMsvcBuildTools = $true }
} catch {
    try {
        $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
        if (Test-Path $vswhere) {
            $vsPath = & $vswhere -latest -property installationPath 2>&1
            if ($vsPath) { $hasMsvcBuildTools = $true }
        }
    } catch { }
}

if (-not $hasMsvcBuildTools) {
    Write-Warn "Microsoft C++ Build Tools not detected."
    Write-Warn "  tree-sitter and its language parsers require MSVC to compile."
    Write-Warn "  Install from: https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    Write-Warn "  (Select 'Desktop development with C++' workload)"
    Write-Warn "  Proceeding with pip install — it may fail if compilation is needed."
}

# Run pip install
try {
    $pipOutput = & $VenvPythonActual -m pip install -e $ProjectRoot 2>&1
    $pipExitCode = $LASTEXITCODE
    if ($pipExitCode -ne 0) {
        Write-Host $pipOutput
        Write-Fail "pip install failed (exit $pipExitCode)."
        Write-Warn "This is often due to missing build tools (MSVC)."
        Write-Warn "Install MSVC Build Tools: https://visualstudio.microsoft.com/visual-cpp-build-tools/"
        Write-Warn "Then re-run this script."
        exit $pipExitCode
    }
} catch {
    Write-Fail "pip install threw an exception: $_"
    exit 1
}

if (Test-Path $VenvCorpus) {
    Write-Ok "corpus-kb installed -> $VenvCorpus"
} else {
    Write-Warn "corpus-kb.exe entry point not found in venv Scripts."
    Write-Warn "  Check pyproject.toml [project.scripts] section."
    Write-Warn "  The package may still be importable. Proceeding ..."
}

# ---------------------------------------------------------------------------
#  8/12  OLLAMA INSTALLATION
# ---------------------------------------------------------------------------
Write-Header "8/12  Ollama"

$ollamaOnPath = $null
try {
    $ollamaOnPath = (Get-Command "ollama.exe" -ErrorAction Stop).Source
} catch {
    # Not on PATH
}

if ($ollamaOnPath) {
    try {
        $ollamaVer = & ollama --version 2>&1
        Write-Ok "Ollama found: $ollamaVer"
    } catch {
        Write-Ok "Ollama found at: $ollamaOnPath"
    }
} else {
    Write-Info "Ollama not found on PATH. Installing ..."

    $ollamaInstalled = $false

    # Method 1: winget
    Write-Info "Method 1: Windows Package Manager (winget) ..."
    try {
        Write-Sub "Installing Ollama.Ollama via winget ..."
        $wingetResult = & winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements 2>&1
        Write-Host $wingetResult
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Ollama installed via winget"
            $ollamaInstalled = $true
        } else {
            Write-Warn "winget install returned exit code $LASTEXITCODE"
        }
    } catch {
        Write-Warn "winget install failed: $_"
    }

    # Method 2: Direct download from ollama.com
    if (-not $ollamaInstalled) {
        Write-Info "Method 2: Direct download from ollama.com ..."
        try {
            $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
            $installerPath = Join-Path $env:TEMP "OllamaSetup.exe"
            Write-Sub "Downloading OllamaSetup.exe ..."
            Invoke-WebRequest -Uri $ollamaUrl -OutFile $installerPath -UseBasicParsing
            Write-Sub "Running Ollama installer (silent mode) ..."
            $proc = Start-Process -FilePath $installerPath -ArgumentList "/S" -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -eq 0) {
                Write-Ok "Ollama installed from ollama.com"
                $ollamaInstalled = $true
            } else {
                Write-Warn "Ollama installer exit code: $($proc.ExitCode)"
            }
        } catch {
            Write-Warn "Download/install from ollama.com failed: $_"
        }
    }

    if (-not $ollamaInstalled) {
        Write-Fail "Could not install Ollama automatically."
        Write-Warn "Please install manually from: https://ollama.com/download/windows"
        Write-Warn "Then re-run this script."
        exit 1
    }

    # Refresh PATH for Ollama
    Refresh-EnvironmentPath
}

# Verify ollama is now on PATH
$ollamaFinalPath = $null
try {
    $ollamaFinalPath = (Get-Command "ollama.exe" -ErrorAction Stop).Source
    Write-Ok "Ollama binary resolved: $ollamaFinalPath"
} catch {
    Write-Fail "Ollama installed but not found on PATH."
    Write-Warn "Close and re-open your terminal, or manually add Ollama to PATH."
    Write-Warn "  Default install path: $env:LOCALAPPDATA\Programs\Ollama"
    exit 1
}

# ---------------------------------------------------------------------------
#  9/12  WAIT FOR OLLAMA + PULL MODEL
# ---------------------------------------------------------------------------
Write-Header "9/12  Ollama health and model pull"

# --- 9a. Ensure Ollama service is running ---
Write-Info "Checking if Ollama is responding ..."
$ollamaReady = Ensure-OllamaServiceRunning

# --- 9b. Poll for health ---
Write-Info "Polling Ollama health at $OLLAMA_HEALTH_URL ..."

$healthOk = $false
$elapsed = 0
$progressChars = @('|', '/', '-', '\')
$charIdx = 0

:healthLoop while ($elapsed -lt $OLLAMA_POLL_TIMEOUT_SEC) {
    try {
        $response = Invoke-WebRequest -Uri $OLLAMA_HEALTH_URL -Method GET -TimeoutSec 3 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            $healthOk = $true
            break healthLoop
        }
    } catch {
        # Not ready yet — keep polling
    }

    Write-Host "`r   $($progressChars[$charIdx % $progressChars.Length]) waiting for Ollama ... ($($elapsed) s)" -NoNewline -ForegroundColor $C_BLUE
    $charIdx++
    Start-Sleep -Seconds $OLLAMA_POLL_INTERVAL_SEC
    $elapsed += $OLLAMA_POLL_INTERVAL_SEC
}

# Clear the spinner line
Write-Host "`r                                                               " -NoNewline
Write-Host "`r"

if (-not $healthOk) {
    Write-Fail "Ollama did not become healthy within $OLLAMA_POLL_TIMEOUT_SEC seconds."
    Write-Warn "Check:"
    Write-Warn "  - Is port 11434 free? (netstat -ano | findstr :11434)"
    Write-Warn "  - Try manually: ollama serve"
    Write-Warn "  - Check Windows Event Viewer for Ollama errors"
    exit 1
}
Write-Ok "Ollama is healthy"

# --- 9c. Pull the embedding model ---
Write-Info "Pulling embedding model '$EMBEDDING_MODEL' (~274 MB) ..."
try {
    & ollama pull $EMBEDDING_MODEL 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ollama pull failed (exit $LASTEXITCODE)" }
    Write-Ok "Model '$EMBEDDING_MODEL' pulled successfully"
} catch {
    Write-Fail "Failed to pull embedding model: $_"
    Write-Warn "Try manually: ollama pull $EMBEDDING_MODEL"
    exit 1
}

# --- 9d. Verify embedding API works ---
Write-Info "Verifying embedding API ..."
try {
    $genBody = @{
        model   = $EMBEDDING_MODEL
        prompt  = "Corpus-KB verification"
        options = @{ embedding_only = $true }
    } | ConvertTo-Json

    $genResponse = Invoke-WebRequest -Uri $OLLAMA_GENERATE_URL -Method POST `
        -Body $genBody -ContentType "application/json" -TimeoutSec 30 -UseBasicParsing

    $genResult = $genResponse.Content | ConvertFrom-Json
    if ($genResult.embedding -or $genResult.done) {
        Write-Ok "Embedding model responds correctly"
    } else {
        Write-Warn "Embedding verification returned unexpected response."
    }
} catch {
    Write-Warn "Embedding verification request failed: $_"
    Write-Warn "  Ollama is running and model is pulled. This is likely transient."
}

# ---------------------------------------------------------------------------
#  10/12  UPDATE MCP CONFIG FILES
# ---------------------------------------------------------------------------
Write-Header "10/12  MCP editor configs"

Write-Info "Updating MCP config files to use absolute venv binary path ..."

# Use an inline Python script for robust JSON manipulation.
# Python is guaranteed available at this point (from the venv).
$pythonScript = @'
import json
import os
import sys

src_dir = sys.argv[1]
venv_bin = sys.argv[2]

# Normalise backslashes for JSON (JSON allows \, but PowerShell
# already passes them through fine).
venv_bin = venv_bin.replace("\\", "/") if "\\" in venv_bin else venv_bin

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
        print(f"  {fname}: not found — skipped")
        continue

    with open(fpath, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  {fname}: invalid JSON ({e}) — skipped")
            errors += 1
            continue

    mutated = False

    # ------------------------------------------------------------------
    # Handle OpenCode format:  { "mcp": { "corpus-kb": { "command": [...] } } }
    # ------------------------------------------------------------------
    mcp_block = data.get("mcp")
    if isinstance(mcp_block, dict):
        for key, server in mcp_block.items():
            changed = False

            # Array command: ["corpus-kb", "--transport", "stdio"]
            cmd_arr = server.get("command")
            if isinstance(cmd_arr, list) and len(cmd_arr) > 0:
                old_first = cmd_arr[0]
                # Replace if it's the bare binary name (no path separator)
                if os.sep not in old_first and "/" not in old_first:
                    cmd_arr[0] = venv_bin
                    changed = True

            # String command (alternative format)
            if not changed:
                cmd_str = server.get("command")
                if isinstance(cmd_str, str) and os.sep not in cmd_str and "/" not in cmd_str:
                    server["command"] = venv_bin
                    changed = True

            if changed:
                print(f"  {fname}/{key}: command -> {venv_bin}")
                mutated = True

    # ------------------------------------------------------------------
    # Handle Cursor/Claude format: { "mcpServers": { "corpus-kb": { ... } } }
    # ------------------------------------------------------------------
    servers_block = data.get("mcpServers")
    if isinstance(servers_block, dict):
        for key, server in servers_block.items():
            changed = False

            # String command: "corpus-kb"
            cmd_str = server.get("command")
            if isinstance(cmd_str, str) and os.sep not in cmd_str and "/" not in cmd_str:
                server["command"] = venv_bin
                changed = True

            # Array command (unlikely in mcpServers but handle defensively)
            cmd_arr = server.get("command")
            if isinstance(cmd_arr, list) and len(cmd_arr) > 0:
                old_first = cmd_arr[0]
                if os.sep not in old_first and "/" not in old_first:
                    cmd_arr[0] = venv_bin
                    changed = True

            # Also check args array for bare binary name
            args = server.get("args")
            if isinstance(args, list):
                for i, arg in enumerate(args):
                    if isinstance(arg, str) and os.sep not in arg and "/" not in arg and arg.endswith("corpus-kb"):
                        args[i] = venv_bin
                        changed = True

            # Check alternative keys (bin, binary, executable)
            for alt_key in ("bin", "binary", "executable"):
                if alt_key in server:
                    val = server[alt_key]
                    if isinstance(val, str) and os.sep not in val and "/" not in val:
                        server[alt_key] = venv_bin
                        changed = True

            if changed:
                print(f"  {fname}/mcpServers.{key}: command -> {venv_bin}")
                mutated = True

    if not mutated:
        # Check if already up-to-date
        already_abs = False
        for block_key in ("mcp", "mcpServers"):
            block = data.get(block_key)
            if isinstance(block, dict):
                for server in block.values():
                    cmd = server.get("command")
                    if isinstance(cmd, str) and cmd == venv_bin:
                        already_abs = True
                    elif isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == venv_bin:
                        already_abs = True
        if already_abs:
            print(f"  {fname}: already up-to-date")
            skipped += 1
            continue
        else:
            print(f"  {fname}: no changes needed (already absolute?)")
            skipped += 1
            continue

    # Write updated JSON
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    updated += 1

print(f"  Result: {updated} updated, {skipped} skipped, {errors} errors")
'@

try {
    $output = & $VenvPythonActual -c $pythonScript $McpSrcDir $VenvCorpus 2>&1
    Write-Host $output
} catch {
    Write-Warn "Failed to update MCP configs: $_"
}

# --- Copy opencode.json -> .opencode\mcp.json ---
$OpenCodeDir = Join-Path $ProjectRoot ".opencode"
$McpOpenCodeSrc = Join-Path $McpSrcDir "opencode.json"
$McpOpenCodeDst = Join-Path $OpenCodeDir "mcp.json"

if (-not (Test-Path $OpenCodeDir)) {
    New-Item -ItemType Directory -Path $OpenCodeDir -Force | Out-Null
}
if (Test-Path $McpOpenCodeSrc) {
    Copy-Item -Path $McpOpenCodeSrc -Destination $McpOpenCodeDst -Force
    Write-Ok "Copied opencode.json -> .opencode\mcp.json"
}

# --- Copy cursor.json -> .vscode\mcp.json ---
$VsCodeDir = Join-Path $ProjectRoot ".vscode"
$McpCursorSrc = Join-Path $McpSrcDir "cursor.json"
$McpCursorDst = Join-Path $VsCodeDir "mcp.json"

if (-not (Test-Path $VsCodeDir)) {
    New-Item -ItemType Directory -Path $VsCodeDir -Force | Out-Null
}
if (Test-Path $McpCursorSrc) {
    Copy-Item -Path $McpCursorSrc -Destination $McpCursorDst -Force
    Write-Ok "Copied cursor.json -> .vscode\mcp.json"
}

# --- Claude Code config (%USERPROFILE%\.claude\mcp.json) ---
$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$McpClaudeSrc = Join-Path $McpSrcDir "claude-code.json"
$McpClaudeDst = Join-Path $ClaudeDir "mcp.json"

if (Test-Path $McpClaudeSrc) {
    if (-not (Test-Path $ClaudeDir)) {
        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
        Copy-Item -Path $McpClaudeSrc -Destination $McpClaudeDst -Force
        Write-Ok "Copied claude-code.json -> $McpClaudeDst"
    } elseif (-not (Test-Path $McpClaudeDst)) {
        Copy-Item -Path $McpClaudeSrc -Destination $McpClaudeDst -Force
        Write-Ok "Copied claude-code.json -> $McpClaudeDst"
    } else {
        Write-Ok "$McpClaudeDst already exists (not overwritten to avoid clobbering user edits)"
    }
} else {
    Write-Ok "Claude Code config template not found — skipping"
}

# ---------------------------------------------------------------------------
#  11/12  SMOKE TEST — RUN DEMO
# ---------------------------------------------------------------------------
Write-Header "11/12  Smoke test (scripts/demo.py)"

if (-not (Test-Path $DemoPy)) {
    Write-Warn "scripts/demo.py not found — skipping smoke test"
} else {
    Write-Info "Running demo.py to verify installation ..."
    Write-Host ""

    try {
        $demoOutput = & $VenvPythonActual $DemoPy 2>&1
        $demoExit = $LASTEXITCODE

        if ($demoExit -eq 0) {
            Write-Ok "Smoke test passed"
        } else {
            Write-Host $demoOutput
            Write-Warn "Demo exited with code $demoExit"
            Write-Warn "Check output above — dependencies or Ollama may need attention."
        }
    } catch {
        Write-Warn "Demo execution threw an exception: $_"
        Write-Warn "Try manually: $VenvPythonActual scripts/demo.py"
    }
}

# ---------------------------------------------------------------------------
#  12/12  DATA DIRECTORIES (idempotent, quick)
# ---------------------------------------------------------------------------
Write-Header "12/12  Data directories"

$dataDirs = @(
    "data\lancedb",
    "data\graph",
    "data\duckdb"
)

foreach ($relDir in $dataDirs) {
    $absDir = Join-Path $ProjectRoot $relDir
    if (-not (Test-Path $absDir)) {
        New-Item -ItemType Directory -Path $absDir -Force | Out-Null
        Write-Ok "Created $relDir\"
    } else {
        Write-Ok "$relDir\ exists"
    }
}

# ---------------------------------------------------------------------------
#  ELAPSED TIME
# ---------------------------------------------------------------------------
$Stopwatch.Stop()
$elapsedTime = $Stopwatch.Elapsed
if ($elapsedTime.TotalMinutes -ge 1) {
    $timeStr = "$([math]::Floor($elapsedTime.TotalMinutes))m $($elapsedTime.Seconds)s"
} else {
    $timeStr = "$($elapsedTime.Seconds)s"
}

# ---------------------------------------------------------------------------
#  SETUP COMPLETE — NEXT STEPS
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════════════════════╗" -ForegroundColor $C_CYAN
Write-Host "║                 Setup Complete!                                            ║" -ForegroundColor $C_CYAN
Write-Host "╚══════════════════════════════════════════════════════════════════════════════╝" -ForegroundColor $C_CYAN
Write-Host ""
Write-Host "   $(CheckMark)  Time elapsed: $timeStr" -ForegroundColor $C_GREEN
Write-Host ""

Write-Host "   === Next Steps ===" -ForegroundColor $C_GREEN
Write-Host ""

# --- MCP Server ---
Write-Host "   -- MCP Server (core) ---------------------------------------" -ForegroundColor $C_CYAN
Write-Host "   " -NoNewline
Write-Host "corpus-kb" -NoNewline -ForegroundColor $C_WHITE
Write-Host "  --  Start the MCP server (stdio)"
Write-Host "   " -NoNewline
Write-Host "corpus-kb --transport sse" -NoNewline -ForegroundColor $C_WHITE
Write-Host "  --  Start in SSE mode"
Write-Host "   " -NoNewline
Write-Host "$VenvCorpus" -ForegroundColor $C_GRAY
Write-Host "               (absolute path, used by editors)"
Write-Host ""

# --- Re-run the demo ---
Write-Host "   -- Re-run the demo -----------------------------------------" -ForegroundColor $C_CYAN
Write-Host "   "
Write-Host "       & $VenvPythonActual scripts/demo.py" -ForegroundColor $C_WHITE
Write-Host "       .venv\Scripts\Activate.ps1 ; python scripts/demo.py" -ForegroundColor $C_GRAY
Write-Host "   "
Write-Host "       Or simply activate:  .venv\Scripts\Activate.ps1" -ForegroundColor $C_GREEN
Write-Host "       Then:                python scripts\demo.py" -ForegroundColor $C_GREEN
Write-Host ""

# --- Editor Integration ---
Write-Host "   -- Editor Integration --------------------------------------" -ForegroundColor $C_CYAN
Write-Host "   "
Write-Host "   OpenCode" -ForegroundColor $C_YELLOW
Write-Host "     Config at:  .opencode\mcp.json" -ForegroundColor $C_WHITE
Write-Host "   "
Write-Host "   VS Code / Cursor / Windsurf" -ForegroundColor $C_YELLOW
Write-Host "     Config at:  .vscode\mcp.json" -ForegroundColor $C_WHITE
Write-Host "     (already set up by this script)" -ForegroundColor $C_GREEN
Write-Host "   "
Write-Host "   Claude Code" -ForegroundColor $C_YELLOW
Write-Host "     Config at:  $env:USERPROFILE\.claude\mcp.json" -ForegroundColor $C_WHITE
Write-Host "     (set up by this script if didn't already exist)" -ForegroundColor $C_GREEN
Write-Host "     Manual: Edit that file to set 'command' to:" -ForegroundColor $C_GRAY
Write-Host "              $VenvCorpus" -ForegroundColor $C_GRAY
Write-Host ""

# --- Useful Commands ---
Write-Host "   -- Useful Commands -----------------------------------------" -ForegroundColor $C_CYAN
Write-Host "   "
Write-Host "  ollama list" -NoNewline -ForegroundColor $C_WHITE
Write-Host "              --  View downloaded models"
Write-Host ('  ollama pull ' + [char]0x3C + 'model' + [char]0x3E) -NoNewline -ForegroundColor $C_WHITE
Write-Host "  --  Download another model"
Write-Host "  ollama ps" -NoNewline -ForegroundColor $C_WHITE
Write-Host "                 --  Show loaded models"
Write-Host "  ollama serve" -NoNewline -ForegroundColor $C_WHITE
Write-Host "              --  Start/restart Ollama"
Write-Host ""

# --- Tips ---
Write-Host "   Tip: Edit config.yaml to change embedding model, chunking, or search." -ForegroundColor $C_YELLOW
Write-Host "   Tip: The default model is nomic-embed-text (768d)." -ForegroundColor $C_YELLOW
Write-Host "   Tip: For better quality, try: ollama pull qwen3-embedding:8b-q8_0" -ForegroundColor $C_YELLOW
Write-Host "        then set model and dimensions in config.yaml." -ForegroundColor $C_YELLOW
Write-Host "   Tip: Always activate .venv before working on the project:" -ForegroundColor $C_YELLOW
Write-Host "        .venv\Scripts\Activate.ps1" -ForegroundColor $C_WHITE
Write-Host ""

Write-Host "   Happy building!" -ForegroundColor $C_GREEN
Write-Host ""

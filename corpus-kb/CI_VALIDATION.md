# CI Validation System

This repo includes a **pre-push validation system** that catches CI failures **locally before pushing to GitHub**, saving time and preventing broken commits.

## Quick Start

**Before any `git push`, run:**

```bash
python .opencode/validate-ci.py
```

Or use OpenCode commands:
```bash
/check-ci          # Quick validation (commit message + configs only)
/pre-push          # Full validation (includes code quality checks)
```

## What Gets Validated

### ✓ Commit Message Format
- Must start with **capital letter** (e.g., `Fix:`, `Add:`, `Setup:`)
- Length: **10–500 characters**
- No dangerous keywords (`force-push`, `filter-branch`, `reset --hard`, `rm -rf`)

**Fails locally if:** `fix: something` (lowercase) or `F` (too short)

### ✓ Config Files (Agent Governance)
- `opencode.json` exists at repo root
- `mcp-configs/cursor.json` exists
- Both are valid JSON

**Fails locally if:** Either file missing or corrupted JSON

### ✓ Code Quality (optional)
- Ruff linting: `ruff check src/`
- Ruff formatting: `ruff format --check src/`
- Type checking: `pyright src/`
- Tests: `pytest tests/`

**Fails locally if:** Any lint, format, type, or test failures

## Validation Modes

### Quick Check (configs + message only, ~1 sec)
```bash
python .opencode/validate-ci.py --skip-code-checks
```
Use before pushing if you haven't changed code.

### Full Check (includes code quality, ~30-60 sec)
```bash
python .opencode/validate-ci.py
```
Use before pushing if you've modified code.

### Manual Validation

If the script isn't available, validate manually:

```bash
# Commit message format
git log -1 --pretty=format:%s | grep -E '^[A-Z].{8,498}$'

# Config files exist and are valid JSON
python -m json.tool opencode.json > /dev/null && echo "✓ opencode.json"
python -m json.tool mcp-configs/cursor.json > /dev/null && echo "✓ cursor.json"

# Code quality (if you changed src/)
ruff check src/
ruff format --check src/
pyright src/
pytest tests/
```

## Common Issues & Fixes

| Error | Fix |
|-------|-----|
| `"Must start with capital letter"` | Change commit message from `fix:` to `Fix:` |
| `"Message too short"` | Keep message between 10–500 characters |
| `"Missing opencode.json"` | Run from repo root; file should exist at `./opencode.json` |
| `"Invalid JSON"` | Check syntax: `python -m json.tool opencode.json` |
| `"ruff check failed"` | Run `ruff check --fix src/` to auto-fix, then retry validation |
| `"pyright failed"` | Run `pyright src/ --outputjson` to see type errors; fix and retry |
| `"Tests failed"` | Run `pytest tests/ -v` to see which tests fail; fix and retry |

## Integration with Your Workflow

**Standard workflow:**

1. Make code changes
2. Run: `python .opencode/validate-ci.py`
3. If ✓ passes → Safe to push
4. If ✗ fails → Fix issues and re-run validation

**Example:**

```bash
# Edit some code
nano src/utils/something.py

# Validate before push
python .opencode/validate-ci.py

# Output:
# ✓ Commit message format valid
# ✓ Config files valid
# ⚠ ruff linting failed
# Run: ruff check --fix src/
# Then: python .opencode/validate-ci.py again

# Fix and re-validate
ruff check --fix src/
python .opencode/validate-ci.py

# Now safe to push
git push
```

## Why This Matters

The GitHub Actions CI workflow is **strict**:
- Governance checks enforce commit message standards
- Agent governance framework requires valid MCP configs on every push
- Code quality gates (linting, typing, tests) are non-negotiable

**Without pre-push validation**, you'll:
- ❌ Push → Wait for GitHub Actions → Fail → Fix → Push again (slow)

**With pre-push validation**, you'll:
- ✅ Validate locally (fast) → Fix immediately → Push once (fast)

## Configuration Files

Validation configuration is stored in:
- `.opencode/opencode.json` — OpenCode command definitions
- `.opencode/ci-gates.json` — CI gate specifications
- `.opencode/validate-ci.py` — Validation script (runnable)
- `.opencode/skills/ci-guard/SKILL.md` — OpenCode skill (trigger on "check ci", "pre-push")

You can modify these if CI rules change, but the Python script is the source of truth for actual validation.

## Future Improvements

When GitHub Actions updates, the validation system will:
1. Fail locally when new rules are added
2. You update `.opencode/validate-ci.py` with the new rules
3. You commit and push the updated validator
4. All future pushes use the new rules

This way, **CI rules are always enforced locally**, even as they evolve.

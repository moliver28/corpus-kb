---
name: ci-guard
description: Pre-push CI validation for corpus-kb. Use before any git push to catch commit message, config file, and code quality issues locally. Triggers on "check ci", "validate ci", "pre-push", "before push", "ci check", "ci gates".
compatibility: opencode
---

# CI Guard - Pre-Push Validation

Validates your corpus-kb changes against the exact GitHub Actions CI gates **before** pushing, catching issues locally instead of on GitHub.

## What It Checks

### 1. **Commit Message Format** (matches GitHub Actions)
- ✓ Starts with capital letter
- ✓ Length: 10–500 characters
- ✗ No dangerous keywords (force-push, filter-branch, reset --hard, rm -rf)

### 2. **Config Files** (agent governance)
- ✓ `opencode.json` exists at repo root with valid JSON
- ✓ `mcp-configs/cursor.json` exists with valid JSON

### 3. **Code Quality** (optional, can skip)
- ✓ Ruff linting: `ruff check src/`
- ✓ Ruff formatting: `ruff format --check src/`
- ✓ Type checking: `pyright src/`
- ✓ Tests: `pytest tests/`

## How to Use

### Quick Check (configs + commit message only)
```bash
python .opencode/validate-ci.py --skip-code-checks
```

### Full Check (includes code quality)
```bash
python .opencode/validate-ci.py
```

### In OpenCode Commands
Use the pre-configured OpenCode commands:

```
/check-ci          # Quick validation (skip code checks)
/pre-push          # Full validation before pushing
```

## Manual Validation Checklist

If the script fails or isn't available, validate manually:

1. **Commit message:**
   ```bash
   git log -1 --pretty=format:%s | grep -E '^[A-Z].{8,498}$'
   ```
   Should print your commit message without error.

2. **Config files:**
   ```bash
   test -f opencode.json && python -m json.tool opencode.json > /dev/null && echo "✓"
   test -f mcp-configs/cursor.json && python -m json.tool mcp-configs/cursor.json > /dev/null && echo "✓"
   ```

3. **Code quality:**
   ```bash
   ruff check src/
   ruff format --check src/
   pyright src/
   pytest tests/
   ```

## Why This Matters

The corpus-kb CI workflow enforces strict governance:
- **Commit Message**: Ensures human-readable history and governance compliance
- **Config Files**: Agent governance framework requires MCP configuration for every push
- **Code Quality**: linting, type safety, and test coverage are non-negotiable

**Without these checks**, your push will fail on GitHub Actions, blocking your work. **Validate locally first** to catch issues in seconds instead of minutes waiting for CI.

## Common Fixes

| Error | Fix |
|-------|-----|
| "Must start with capital letter" | Change `fix:` to `Fix:` in commit message |
| "Too short/long" | Keep message 10–500 chars |
| "Missing opencode.json" | Run from repo root or check file exists |
| "Invalid JSON" | Validate: `python -m json.tool file.json` |
| "ruff check failed" | Run `ruff check --fix src/` to auto-fix linting issues |
| "pyright failed" | Check type errors: `pyright src/ --outputjson` |
| "pytest failed" | Review failing tests or run `pytest tests/ -v` for details |

## Integration with Your Workflow

**Before pushing ALWAYS run:**
```bash
# In corpus-kb directory:
python .opencode/validate-ci.py
```

Or use OpenCode command:
```
/pre-push
```

If validation passes: **safe to push**.
If validation fails: **fix the issues and re-run** before pushing.

This prevents wasted time waiting for GitHub CI to reject your work.

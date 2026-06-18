# CI Config Validation

OpenCode runs automated validation on all MCP configuration files in CI. This catches format bugs before they reach users.

## How It Works

The validation script (`scripts/validate_configs.py`) runs as the first job in the CI pipeline. It checks three things:

1. **Schema validation per format** — each config file is validated against its expected structure. The root `opencode.json` uses the OpenCode native format. Files under `mcp-configs/` are validated against their respective editor formats.

2. **Cross-config consistency** — all configs must define the same set of tools with matching descriptions and auto-approve lists (where applicable).

3. **Fail-fast behavior** — the validation job runs before the Ollama setup and test suite. A config error stops CI immediately, saving time.

The script exits with code 1 and prints all found errors on any violation.

## Supported Formats

### OpenCode (native)

**Files:** `opencode.json` (root), `mcp-configs/opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "tool-name": {
      "type": "local",
      "description": "Tool description",
      "command": ["executable", "--flag", "value"],
      "environment": {},
      "autoApprove": ["tool1", "tool2"]
    }
  }
}
```

Key points:
- Uses the `mcp` key (not `mcpServers`).
- `command` is an array of strings.
- `autoApprove` is a list of valid tool names from `src/tools/`.
- `$schema` URL is required.

### Claude Code

**File:** `mcp-configs/claude-code.json`

```json
{
  "mcpServers": {
    "tool-name": {
      "name": "Tool Name",
      "description": "Tool description",
      "command": "executable",
      "args": ["--flag", "value"],
      "env": {},
      "disabled": false,
      "autoApprove": ["tool1", "tool2"]
    }
  }
}
```

Key points:
- Uses the `mcpServers` key.
- `command` is a single string. Arguments go in a separate `args` array.
- `autoApprove` is required and must match the OpenCode format list.

### Cursor

**File:** `mcp-configs/cursor.json`

```json
{
  "mcpServers": {
    "tool-name": {
      "name": "Tool Name",
      "description": "Tool description",
      "command": "executable",
      "args": ["--flag", "value"],
      "env": {}
    }
  }
}
```

Key points:
- Uses the `mcpServers` key.
- `command` is a single string. Arguments go in a separate `args` array.
- No `autoApprove` field (Cursor does not support it).

## Adding a New Config Format

To add support for a new editor or platform:

1. **Create the config file** at `mcp-configs/<platform>.json` following that platform's MCP server specification.

2. **Add a validator** in `scripts/validate_configs.py`. Create a new validation function that checks:
   - The correct top-level key (`mcpServers` or equivalent).
   - Required fields per entry (name, description, command, args).
   - Platform-specific constraints (e.g., whether `autoApprove` is supported).
   - The `command` field type (string vs array).

3. **Register the validator** in the main validation loop so it runs against the new file.

4. **Add cross-config checks** if the new format supports auto-approve or descriptions. The tool set and descriptions should match across all formats.

5. **Write tests** in `tests/test_validate_configs.py` covering:
   - Valid config for the new format passes.
   - Missing required fields fail.
   - Wrong field types fail.
   - Cross-config mismatch with other formats fails.

## Running Validation Locally

Run the validation script directly:

```bash
python scripts/validate_configs.py
```

Run the test suite:

```bash
pytest tests/test_validate_configs.py -v
```

Both should pass before you push. The CI pipeline runs these automatically on every push and pull request to `master`.

## Troubleshooting Common Errors

### "Expected `mcp` key, found `mcpServers`"

The root `opencode.json` uses the OpenCode native format with an `mcp` key. If you see this error, you are using the old `mcpServers` format. Migrate to the new format:

- Rename `mcpServers` to `mcp`.
- Change `command` from a string to an array.
- Add `type: "local"` to each entry.
- Add an `environment` object (can be empty).

### "Missing `autoApprove`"

OpenCode and Claude Code configs require an `autoApprove` list. Cursor does not. If your OpenCode or Claude Code config is missing this field, add it with the list of tool names that should be auto-approved.

### "Invalid tool name in `autoApprove`"

Every value in `autoApprove` must correspond to an actual tool defined in `src/tools/`. Check the tool name for typos.

### "Mismatched descriptions between configs"

All config formats must have the same description for each tool. If you updated the description in one file, update it in all of them.

### "Mismatched tool sets"

Every config must define the same set of tools. If you added a tool to `opencode.json`, add it to `mcp-configs/claude-code.json` and `mcp-configs/cursor.json` as well.

### "Wrong `command` type"

OpenCode uses an array for `command`. Claude Code and Cursor use a string for `command` with a separate `args` array. Make sure you are using the right type for each format.

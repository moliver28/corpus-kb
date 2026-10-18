# Harness distribution: the corpus command pack

Corpus-KB ships its research/coding surfaces to AI-agent harnesses as a
GENERATED command pack. The one source of truth is
[`src/corpus_kb/surface_registry.py`](../src/corpus_kb/surface_registry.py)
(strictly stdlib-only); `scripts/gen_harness_pack.py` emits everything from
it. **Never hand-edit a generated wrapper** — `gen_harness_pack.py --check`
(which `scripts/check_agent_configs.py` runs internally) fails the bar on
drift.

## What is generated (and committed)

| Artifact | Harness | Notes |
|----------|---------|-------|
| `.claude/commands/corpus-*.md` | Claude Code | YAML front-matter (`description`, `argument-hint`) + registry body |
| `.opencode/command/corpus-*.md` | OpenCode | description-only front-matter, identical bodies |
| `mcp-configs/codex.json` | Codex CLI | copy-template with the same `mcpServers` shape as `claude-code.json` |
| `.claude/CLAUDE.md` | Claude Code | research-commands section between `corpus-harness:research-commands:*` marker fences |

Wrapper bodies contain ONLY invocation guidance and `corpus-kb <cmd>
--help` pointers — zero duplicated logic. Any other MCP-compatible harness
(Hermes, Cline, ...) connects through the existing MCP-stdio server with no
extra files.

## Regenerate / drift gate

```bash
python scripts/gen_harness_pack.py            # regenerate from the registry
python scripts/gen_harness_pack.py --check    # exit 1 on drift
python scripts/check_agent_configs.py         # config parity + harness parity
```

Both scripts (and the registry itself) run on a bare interpreter with NO
dependencies installed — the `agent-config-consistency` CI job installs
nothing.

## Tool-name lockstep (r9)

Every PR that registers an MCP tool must, in the SAME PR:

1. extend `VALID_TOOL_NAMES` in
   `src/corpus_kb/_setup/validate_configs.py` (else the validate-configs CI
   job errors on unknown autoApprove entries), and
2. regenerate the harness pack (`python scripts/gen_harness_pack.py`) and
   commit the diff.

The surface registry is the single place where CLI subcommand paths, MCP
tool names, and wrapper bodies meet. The test `tests/test_harness_pack.py`
asserts two things. The generated files match the registry AS DATA. Every
registry MCP tool name is autoApprove-valid.

## Recommended consumer-side distributor: quiver-cli

For teams installing corpus packs across several harnesses (OpenCode,
Claude Code, Codex), we recommend
[quiver-cli](https://github.com/nbialk/quiver-cli). It composes skills,
slash commands, and MCP servers from a central catalog into any repo as
NATIVE configs. A `quiver.lock` drift baseline (`quiver-cli sync` /
`quiver-cli check --offline`) also detects MCP tool-description tampering
(description-poisoning defense). Runtime output stays native per
harness — there is no extra agent runtime.

**This is documentation only — quiver-cli is NOT a dependency of
Corpus-KB.** The wiring is trivial precisely because everything above is
generated from `surface_registry.py`. A consumer catalog entry points at
the same artifacts this repo already commits (r9 wiring note: no dependency
added). `mcify` was evaluated and rejected as an alternative. It is a greenfield
MCP framework that would REPLACE the existing FastMCP/Starlette stack
rather than sync it.

### Catalog entries a consumer adds

The ten corpus commands plus the corpus MCP server — all shipped. Names
sourced from `surface_registry.py` (`WRAPPERS` / `SURFACES`):

| Catalog entry | Kind | Source surface(s) | Status |
|---------------|------|-------------------|--------|
| `corpus-ingest` | command | `research ingest-transcript`, `research ingest` | shipped |
| `corpus-coding-run` | command | `coding run` | shipped |
| `corpus-codebook-promote` | command | `codebook promote` | shipped |
| `corpus-research-report` | command | `research report` | shipped |
| `corpus-review` | command | `review accept`, `review override` | shipped |
| `corpus-demo` | command | `research demo` | shipped |
| `corpus-research-cycle` | command | `research cycle` | shipped |
| `corpus-release` | command | `release assess/request/review/execute/status/compare` | shipped |
| `corpus-ingest-front-door` | command | `ingest`, `ingest-status`, `ingest-reembed` | shipped |
| `corpus-kb` | MCP server | all `SURFACES` `.mcp_tool` names (stdio transport) | shipped |

New wrappers come from the registry: add the `Wrapper` row and run
`gen_harness_pack.py`. The catalog entries above then resolve to real
files with no doc change.

# Corpus-KB Agent Instructions

## Repository Map

A full codemap is available at `codemap.md` in the project root.

Before working on any task, read `codemap.md` to understand:
- Project architecture and entry points
- Directory responsibilities and design patterns
- Data flow and integration points between modules

For deep work on a specific folder, also read that folder's `codemap.md`:

| Folder | Codemap |
|--------|--------|
| Root | `codemap.md` |
| src/ | `src/codemap.md` |
| src/chunking/ | `src/chunking/codemap.md` |
| src/storage/ | `src/storage/codemap.md` |
| src/rag/ | `src/rag/codemap.md` |
| src/tools/ | `src/tools/codemap.md` |
| src/utils/ | `src/utils/codemap.md` |
| scripts/ | `scripts/codemap.md` |

## Working Conventions

- Python 3.11+ only. No type: ignore. No Any where a real type works.
- 250-line soft limit on source files. Split if exceeded.
- TDD for new features. Tests in tests/ mirroring src/ structure.
- The abstract GraphStore interface is the pattern for swappable backends.
- Default embedding model: nomic-embed-text (768d). Upgradeable to qwen3-embedding:8b-q8_0 (4096d).

## Git Safety

**CRITICAL: AI agents must NEVER execute destructive git operations.** The following commands are strictly forbidden:

### Forbidden Commands (DENY)
- `git reset --hard` — wipes uncommitted work and can lose committed history
- `git push --force` / `git push --force-with-lease` — overwrites remote history
- `git clean -fd` / `git clean -fdx` — permanently deletes untracked files
- `git filter-branch` — rewrites history destructively

### Ask-First Commands
- `rm -rf` / `Remove-Item -Recurse -Force` — irreversible file deletion
- `sudo` — elevated privileges
- `del /s /q` — Windows recursive delete

### Recovery Protocol
If a reset accidentally wipes local commits:
1. Check `git reflog` for the lost commit SHA
2. Run `git reset --hard <sha>` to restore
3. If already pushed: `git reset --hard origin/master` to sync with remote

### Golden Rule
**Push before any major git operation.** If work is on the remote, it's recoverable. If it's only local, a reset can destroy it permanently.

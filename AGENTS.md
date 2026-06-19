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

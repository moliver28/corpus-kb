# Contributing to corpus-kb

Thanks for taking the time to improve **corpus-kb**. This guide covers local
development, testing, commit message conventions, and the pull-request
process. For deeper architecture notes, read [`AGENTS.md`](AGENTS.md) and the
docs under `corpus-kb/docs/`.

## Local development

1. **Install the project (editable) with dev extras:**

   ```bash
   pip install -e corpus-kb/.[dev]
   ```

   Or, if you use [uv](https://docs.astral.sh/uv/):

   ```bash
   uv sync --extra dev
   ```

2. **Install the pre-commit hooks** so commits are auto-formatted and
   validated before they reach CI:

   ```bash
   pip install pre-commit
   pre-commit install
   pre-commit run --all-files   # one-time full sweep
   ```

3. **Active source lives under `corpus-kb/src/`.** The legacy `src/` directory
   at the repo root is frozen and must not be edited (see `AGENTS.md`).
   Imports under `corpus-kb/src/` are relative; tests use absolute
   `from src.xxx` imports.

## Running tests

Integration tests mock Ollama with the zero-vector fallback, so the suite
degrades gracefully when Ollama is offline.

```bash
# Unit + integration tests
python -m pytest corpus-kb/tests/ -v --tb=short --durations=10

# With coverage
pytest --cov=corpus-kb/src --cov-report=term-missing
```

To exercise the embedder end-to-end, start Ollama locally and pull the
default model:

```bash
ollama serve &
ollama pull nomic-embed-text
```

## Linting and formatting

We use [`ruff`](https://docs.astral.sh/ruff/) for both linting and formatting
(no `black`, no `flake8`). Run these from the `corpus-kb/` directory:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

The CI pipeline also runs:

- `pyright` (basic mode — see `corpus-kb/pyrightconfig.json`)
- `python scripts/validate_configs.py` to validate MCP configs

Both gates must pass before a PR can be merged.

## Commit message conventions

This repo enforces [Conventional Commits](https://www.conventionalcommits.org/).
The CI workflow `.github/workflows/commit-validate.yml` rejects any message
that does not match.

**Allowed prefixes (no others):**

| Prefix     | Use for                                          |
| ---------- | ------------------------------------------------ |
| `feat`     | New user-facing functionality                     |
| `fix`      | Bug fix                                           |
| `docs`     | Documentation only                                |
| `chore`    | Tooling, deps, config; no production code change |
| `test`     | Adding or fixing tests                           |
| `refactor` | Code change with no behavior change              |
| `perf`     | Performance improvement                          |

**Format:**

```text
<prefix>(<scope>)?: <imperative summary>
```

- The summary starts with a capital letter and is **10–500 characters**.
- An optional scope names the affected area (e.g. `feat(rag): ...`,
  `fix(installer): ...`). Keep scopes short and stable.
- The body and footers must also begin with a conventional commit prefix or
  a recognised footer (`Co-authored-by:`, `BREAKING CHANGE:`, etc.) — the
  validator checks every line.

**Examples:**

```text
feat(rag): add hybrid search with RRF fusion
fix(installer): resume from last completed phase on retry
docs: clarify Postgres prerequisite in README
chore: bump ruff to 0.6.9
```

## Pull-request process

1. Create a topic branch from `master`:
   `git checkout -b feature/<short-topic>` or `bugfix/<short-topic>`.
2. Make focused commits following the conventions above.
3. Before pushing, run locally:

   ```bash
   ruff check src/ tests/
   ruff format --check src/ tests/
   pytest corpus-kb/tests/
   ```

4. Open a pull request using the provided
   [PR template](.github/PULL_REQUEST_TEMPLATE.md). Link the relevant plan or
   issue, describe the change, and tick every applicable checklist item.
5. CI must be green on all three operating systems (`ubuntu`, `windows`,
   `macos`) before review.
6. At least one approval from a code owner (see
   [`.github/CODEOWNERS`](.github/CODEOWNERS)) is required before merge.
   Squash-merge is the default; rebases are preferred for linear history.

## Reporting issues

Use GitHub Issues. For security concerns, see `SECURITY.md` (TBD) or contact
the maintainers directly — **do not** file public issues for suspected
vulnerabilities.

## Code of conduct

Be respectful. Assume good faith. Focus on the technical merits.

## Summary

<!-- One or two sentences: what does this PR change and why? -->

## Linked plan / issue

<!-- Link the plan section or GitHub issue this PR implements.
     If none, write "None". -->

- Plan: <!-- e.g. .omo/plans/installation-ux-ci-quality-overhaul.md#todo-12 -->
- Issue: <!-- #NNN or "None" -->

## Scope

<!-- Check exactly one. Delete the others. -->

- [ ] `feat` — new user-facing functionality
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `chore` — tooling, deps, or config
- [ ] `test` — tests only
- [ ] `refactor` — code change with no behavior change
- [ ] `perf` — performance improvement

## What changed

<!-- Bulleted list of files / modules touched and the high-level effect. -->

-
-
-

## Testing

- [ ] I added or updated unit tests for the change
- [ ] I added or updated integration tests where relevant
- [ ] Local run: `pytest corpus-kb/tests/` passes
- [ ] New or modified public functions have docstrings / type hints

## Lint & format

Run from the `corpus-kb/` directory:

- [ ] `ruff check src/ tests/` passes
- [ ] `ruff format --check src/ tests/` passes

## Pre-commit hooks

- [ ] `pre-commit install` has been run on my machine
- [ ] `pre-commit run --all-files` passes

## CI gates

- [ ] Lint job passes (ruff)
- [ ] Type-check job passes (pyright)
- [ ] `validate_configs.py` passes
- [ ] Test job passes on `ubuntu`, `windows`, and `macos`

## Migration / breaking changes

<!-- If any. Otherwise: "None". -->

- [ ] No database migrations required
- [ ] No API breaking changes
- [ ] No config-file format changes

## Documentation

- [ ] `README.md` is updated (if user-facing)
- [ ] Relevant docs under `corpus-kb/docs/` are updated
- [ ] `AGENTS.md` is updated (if conventions changed)

## Reviewer notes

<!-- Anything reviewers should pay particular attention to: design choices,
     trade-offs, follow-ups left for later, screenshots, etc. -->

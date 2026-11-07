#!/bin/sh
# Shared hook environment (issue #352): find a Python for the hook bodies.
# CORPUS_KB_HOOK_PYTHON wins (tests use it), then python3, then python.
# Fail open: without any Python the hooks exit 0.

hook_python() {
  if [ -n "$CORPUS_KB_HOOK_PYTHON" ]; then
    printf '%s\n' "$CORPUS_KB_HOOK_PYTHON"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

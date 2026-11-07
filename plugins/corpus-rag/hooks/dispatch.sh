#!/bin/sh
# One dispatcher for the corpus-rag hook events (issues #352, #353).
# Usage: dispatch.sh <hook-name>  — runs hooks/<hook-name>.py.
#
# The in-session events walk up from $PWD for .claude/corpus.json first
# (guard.sh) and exit 0 outside a study folder, so Python never starts
# there. UserPromptSubmit also passes in a research folder that holds the
# .claude/corpus-research sentinel, for the setup offer. SessionStart
# always runs: it does the daily prune and the setup offer. Stop always
# runs but is cheap and prints nothing when no session record exists.
#
# Fail open: no dispatcher file, no body script, or no Python -> exit 0.

f="$0"
[ -f "$f" ] || exit 0
hook_name="$1"
[ -n "$hook_name" ] || exit 0
hook_dir=$(dirname "$f")
body="$hook_dir/$hook_name.py"
[ -f "$body" ] || exit 0

. "$hook_dir/hook_env.sh"

case "$hook_name" in
  session_start | stop) ;;
  *) . "$hook_dir/guard.sh" "$hook_name" ;;
esac

hook_py=$(hook_python) || exit 0
exec "$hook_py" "$body"

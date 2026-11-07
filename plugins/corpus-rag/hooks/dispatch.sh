#!/bin/sh
# One dispatcher for the corpus-rag hook events (issue #352).
# Usage: dispatch.sh <hook-name>  — runs hooks/<hook-name>.py.
#
# The five in-session events walk up from $PWD for .claude/corpus.json
# first (guard.sh) and exit 0 outside a study folder, so Python never
# starts there. SessionStart always runs: it does the daily prune and the
# research setup offer. Stop always runs but is cheap and prints nothing
# when no session record exists.
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
  *) . "$hook_dir/guard.sh" ;;
esac

hook_py=$(hook_python) || exit 0
exec "$hook_py" "$body"

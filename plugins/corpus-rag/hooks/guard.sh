#!/bin/sh
# Study-folder guard for the five in-session hook events (issue #352):
# UserPromptSubmit, SubagentStart, PreToolUse, PostToolUse, FileChanged.
# Walks up from $PWD for .claude/corpus.json and exits 0 before Python
# starts when the file is absent or holds "enabled": false. Hook cost
# outside study folders stays near zero.

corpus_guard_dir="$PWD"
while :; do
  if [ -f "$corpus_guard_dir/.claude/corpus.json" ]; then
    if grep -q '"enabled"[[:space:]]*:[[:space:]]*false' \
        "$corpus_guard_dir/.claude/corpus.json" 2>/dev/null; then
      exit 0
    fi
    break
  fi
  corpus_guard_parent=$(dirname "$corpus_guard_dir" 2>/dev/null) || exit 0
  [ "$corpus_guard_parent" = "$corpus_guard_dir" ] && exit 0
  corpus_guard_dir="$corpus_guard_parent"
done

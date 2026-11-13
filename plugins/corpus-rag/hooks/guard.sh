#!/bin/sh
# Study-folder guard for the in-session hook events (issues #352, #353).
#
# SubagentStart, PreToolUse, PostToolUse, and FileChanged walk up from
# $PWD for .claude/corpus.json and exit 0 before Python starts when the
# file is absent or holds "enabled": false. Hook cost outside study
# folders stays near zero.
#
# UserPromptSubmit additionally passes when a walk-up level holds the
# research sentinel (.claude/corpus-research), so the setup offer can
# match the trigger list in a research folder. The sentinel exists only
# where the offer or `corpus-rag signals` found transcripts; plain
# folders still exit 0 before Python starts.

is_prompt="$1"
corpus_guard_dir="$PWD"
while :; do
  if [ -f "$corpus_guard_dir/.claude/corpus.json" ]; then
    if grep -q '"enabled"[[:space:]]*:[[:space:]]*false' \
        "$corpus_guard_dir/.claude/corpus.json" 2>/dev/null; then
      exit 0
    fi
    break
  fi
  if [ "$is_prompt" = "prompt_submit" ] \
     && [ -f "$corpus_guard_dir/.claude/corpus-research" ]; then
    break
  fi
  corpus_guard_parent=$(dirname "$corpus_guard_dir" 2>/dev/null) || exit 0
  [ "$corpus_guard_parent" = "$corpus_guard_dir" ] && exit 0
  corpus_guard_dir="$corpus_guard_parent"
done

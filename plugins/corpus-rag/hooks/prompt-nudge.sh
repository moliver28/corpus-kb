#!/bin/sh
# UserPromptSubmit nudge for the corpus-rag plugin.
# Fail-open: every error path exits 0, so a broken hook never blocks a prompt.
# Stdin: one JSON object with the user "prompt". stdout text joins the prompt.

# Fail-open guard: no state file, no work.
f="$0"
[ -f "$f" ] || exit 0

input=$(cat 2>/dev/null) || input=""
prompt=$(printf '%s' "$input" | sed -n 's/.*"prompt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
[ -n "$prompt" ] || exit 0

# Plugin state dir. Tests override this with CORPUS_KB_PLUGIN_STATE_DIR.
state="${CORPUS_KB_PLUGIN_STATE_DIR:-$HOME/.corpus-kb/plugin}"

# Hints fire only once a study is registered (see the corpus-ingest skill).
[ -f "$state/study-registered" ] || exit 0

mkdir -p "$state/marks" 2>/dev/null || exit 0
today=$(date +%Y%m%d 2>/dev/null) || today="unknown"

# offer LANE TEXT: print TEXT once per lane per day. A declined mark or a
# same-day mark silences the lane. Only one lane fires per prompt.
offer() {
  lane="$1"
  [ -f "$state/marks/declined-$lane" ] && return 0
  mark="$state/marks/prompt-$lane-$today"
  [ -f "$mark" ] && return 0
  : > "$mark" 2>/dev/null || return 0
  printf '%s\n' "$2"
  exit 0
}

# Lane: analysis uncertainty (checked first; the most specific lane).
if printf '%s' "$prompt" | grep -iqE 'which test|significant|correlation|how do i analyze|statistical'; then
  offer analyze 'Not sure which analysis fits your question? Run: corpus-kb analysis-select-method. It names a method and explains why.'
fi

# Lane: files that want ingesting.
if printf '%s' "$prompt" | grep -iqE 'ingest|\.pdf|\.vtt|\.srt|transcript'; then
  offer ingest 'Add transcripts, meeting notes, websites, PDF, PPTX, or DOCX files with: corpus-kb ingest <path>. Google Slides: export to .pptx first, then ingest. The corpus-ingest skill has the details.'
fi

# Lane: questions (a registered study means the corpus can answer with sources).
if printf '%s' "$prompt" | grep -iqE '\?|^what |^why |^how |^when |^where |^who |^which |^can |^does |^is |^are |^did |^do '; then
  offer ask 'Your corpus can answer this with citations. Run: corpus-kb ask "<your question>". To scout first: corpus-kb search "<words>".'
fi

# No lane matched: stay silent.
exit 0

#!/bin/sh
# SessionStart nudge for the corpus-rag plugin.
# Fail-open: every error path exits 0, so a broken hook never blocks a session.
# Stdin: one JSON object with the session "cwd". stdout text joins the session.

# Fail-open guard: no state file, no work.
f="$0"
[ -f "$f" ] || exit 0

input=$(cat 2>/dev/null) || input=""
cwd=$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
[ -n "$cwd" ] || exit 0

# Plugin state dir. Tests override this with CORPUS_KB_PLUGIN_STATE_DIR.
state="${CORPUS_KB_PLUGIN_STATE_DIR:-$HOME/.corpus-kb/plugin}"

# User said no to session hints.
[ -f "$state/marks/declined-session" ] && exit 0

# Lane gate: only sessions inside a Research/<project> folder.
# "?*" demands at least one project-name character after Research/.
case "$cwd" in
  *[/\\]Research[/\\]?*) ;;
  *) exit 0 ;;
esac

# A registered study silences this hint.
[ -f "$state/study-registered" ] && exit 0

# One offer per project per day (dated mark keyed by the cwd checksum).
key=$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)
today=$(date +%Y%m%d 2>/dev/null) || today="unknown"
mark="$state/marks/session-$key-$today"
[ -f "$mark" ] && exit 0

mkdir -p "$state/marks" 2>/dev/null || exit 0
: > "$mark" 2>/dev/null || exit 0

cat <<'EOF'
This folder looks like a research project with no corpus study yet.
Start one: run `corpus-kb ingest <folder>` to load the files.
Then ask: `corpus-kb ask "<question>"` answers with citations.
The corpus-ingest skill has the exact steps.
Do not want these hints? Create the file marks/declined-session in
your plugin state folder and they stop.
EOF
exit 0

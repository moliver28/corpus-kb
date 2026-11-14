# Chat

Chat (Phase-2 Wave 7) answers questions about your corpus. It streams a
cited answer, and the desk rail opens it. This page is the owner's
setup and the rung contract.

## The rungs

`chat.backend` picks the rung:

| Rung | State | Who pays |
|---|---|---|
| `claude-agent-sdk` | wired | Your Claude subscription (default) |
| `ollama` | wired | Your local Ollama (the ask machinery, multi-turn) |
| `anthropic-api` | refused at session start | A follow-up issue tracks the wiring |

The claude-agent-sdk rung is for ONE owner on ONE machine. The config
load refuses it when `server.host` is not a loopback address or
`chat.single_owner` is not true — no silent rung switch, ever. Use
`ollama` for a shared server.

## The subscription rung: how billing stays yours

The app never touches claude.ai login and never removes an auth method.
Three rules protect the subscription billing path:

1. **The work-account pin.** The agent runs with
   `CLAUDE_CONFIG_DIR=<chat.claude.config_dir>` (default
   `~/.claude-corpus`), so your personal Claude Code login is never the
   account the server uses. Set `chat.claude.config_dir` for a
   different pin.
2. **The precedence refusal.** Any of `ANTHROPIC_API_KEY`,
   `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`,
   `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY` in the server
   env outranks the subscription login. Session start refuses with a
   typed error naming the variable; the doctor shows a WARN. Unset the
   variable for this server, or set `chat.backend` to `ollama`.
   The env is never scrubbed.
3. **The headless escape.** No browser login on a server: mint a
   one-year token with `claude setup-token` and feed it through the env
   var:

   ```
   claude setup-token
   export CORPUS_KB_CHAT_CLAUDE_OAUTH_TOKEN=your-token
   ```

   The token fills `chat.claude.oauth_token` at load time. It is never
   stored in a file.

## Setup

1. Install the extra: `pip install -e ".[chat]"`.
2. Log the work account in:

   ```
   CLAUDE_CONFIG_DIR=~/.claude-corpus claude
   ```

   then run `/login` inside that session.
3. Set `chat.enabled: true` in config.yaml. Restart the server.
4. Open Chat in the desk rail. `corpus-kb doctor` prints the chat
   checks; the Chat page shows the same truth when a check blocks.

Headless hosts skip step 2 and use `claude setup-token` (above).

### ARM64 Windows

The SDK ships no ARM64-Windows binary. Install Claude Code natively,
then point the rung at it with `chat.claude.cli_path`. `corpus-kb
doctor` names this fix when the probe fails.

## What the agent may do

Deny-by-default: the agent sees exactly one tool,
`mcp__corpus__search`, a read-only search over your corpus. The
auto-allowed read-only file tools (Read, Glob, Grep, LS, NotebookRead)
are denied by name. So are every write, shell, and web tool. A per-call
hook denies anything outside the allow list, including a call no rule
would have prompted for. Chat writes nothing to the corpus.

## Sessions

`chat.max_sessions` (default 1) caps live sessions; one Claude session
holds about 1 GiB RAM. An idle session closes after
`chat.session_ttl_s` (default 1800 s). The transcript stays on disk
under the pinned working directory (`installer.data_dir`), and the
Sessions list resumes it. Stop is a client abort: the server
interrupts the run, drains the receipt, and closes.

## The release ritual (ToS drift)

The self-hosted single-owner carve-out for subscription use is a
policy page that can move. Each release:

1. Re-check
   `https://code.claude.com/docs/en/legal-and-compliance` and note the
   page's `lastModified`.
2. If the end-user carve-out moved, flip the default guidance to the
   api-key or ollama rung and record the change here.
3. Re-verify the authentication precedence list
   (`code.claude.com/docs/en/authentication`) against
   `PRECEDENCE_ENV_VARS` in `src/corpus_kb/chat/claude_rung.py`; the
   refusal list must name exactly the variables ranked above the
   subscription login.

Live check on 2026-11-13: the carve-out held. Conditions: an unmodified
binary, no auth-method removal, no login changes, per-user
credentials.

#!/usr/bin/env python3
"""PostToolUse hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_turn

    raise SystemExit(hook_main.run_event("PostToolUse", hook_turn.run_post_tool_use))

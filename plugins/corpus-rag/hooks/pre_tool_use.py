#!/usr/bin/env python3
"""PreToolUse hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_watch

    raise SystemExit(hook_main.run_event("PreToolUse", hook_watch.run_pre_tool_use))

#!/usr/bin/env python3
"""SubagentStart hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_events

    raise SystemExit(hook_main.run_event("SubagentStart", hook_events.run_subagent_start))

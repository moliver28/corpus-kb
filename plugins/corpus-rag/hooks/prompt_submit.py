#!/usr/bin/env python3
"""UserPromptSubmit hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_events

    raise SystemExit(hook_main.run_event("prompt_submit", hook_events.run_prompt_submit))

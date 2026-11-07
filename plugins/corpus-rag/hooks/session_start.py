#!/usr/bin/env python3
"""SessionStart hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_session

    raise SystemExit(hook_main.run_event("SessionStart", hook_session.run_session_start))

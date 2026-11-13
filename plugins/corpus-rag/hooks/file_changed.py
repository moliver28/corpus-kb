#!/usr/bin/env python3
"""FileChanged hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_turn

    raise SystemExit(hook_main.run_event("FileChanged", hook_turn.run_file_changed))

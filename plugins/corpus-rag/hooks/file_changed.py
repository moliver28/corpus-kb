#!/usr/bin/env python3
"""FileChanged hook body (issue #352). See dispatch.sh and hook_main.run_event."""

import hook_main

if __name__ == "__main__":
    import hook_watch

    raise SystemExit(hook_main.run_event("file_changed", hook_watch.run_file_changed))

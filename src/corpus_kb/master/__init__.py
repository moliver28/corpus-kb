"""Master topology helpers (W5): the active-state rule and its store.

``active`` holds the pure one-active-project rule; ``attribution`` maps a
path to its project slug; ``state_store`` reads and writes the cached
state, the activity stamps, and the sidecar file through the storage
gateway. Handlers, tools, and the doctor import this package; it imports
storage only (the layering rule keeps the arrow one way).
"""

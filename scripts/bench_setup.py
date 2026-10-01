"""Placeholder bench setup harness for Corpus-KB.

This module is intentionally minimal until a dedicated benchmark suite is
added. It exists so CI and local workflows have a stable ``scripts/bench_setup.py``
entry point to invoke.
"""

from __future__ import annotations


def main() -> None:
    """Print a placeholder message and exit."""
    print("bench_setup.py: no benchmarks configured")


if __name__ == "__main__":
    main()

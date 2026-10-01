"""Backward-compatibility shim for the old scripts/install.py entry point.

This file is preserved so existing documentation and muscle memory keep
working, but new users should run ``corpus-kb setup`` (or ``corpus-kb doctor``)
instead.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    print(
        "scripts/install.py is deprecated; use `corpus-kb setup` instead.",
        file=sys.stderr,
    )
    # Editable-install safety: make sure the repo's src/ tree is importable
    # when this script is invoked directly without an installed package.
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = str(repo_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from corpus_kb._setup.install import main as _install_main

    return _install_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

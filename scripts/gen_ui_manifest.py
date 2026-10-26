#!/usr/bin/env python3
"""Backward-compatibility shim: the manifest generator lives in the package.

The real module is ``src/corpus_kb/_setup/gen_ui_manifest.py`` — it imports
the badge threshold from ``corpus_kb.config``, so it must sit inside the
package for the dead-code gate to see that consumer.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # Editable-install safety: make sure the repo's src/ tree is importable
    # when this script is invoked directly without an installed package.
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = str(repo_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from corpus_kb._setup.gen_ui_manifest import main as _generate

    return _generate(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

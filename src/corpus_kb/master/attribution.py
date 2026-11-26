"""Workspace attribution (W5-2): map a path to its project slug.

The workspace root's first-level folders name projects. The rule is
mechanical: no prompt, no I/O — an unknown slug later auto-registers one
project row named after the slug (spec s3). Pure: safe on odd input.
"""

from __future__ import annotations

import os
import re
from typing import Any, cast

_SLUG_RE = re.compile(r"^[-a-zA-Z0-9]+$")


def master_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The ``master`` block of a config dict, tolerating absent trees.

    One shared reader: the store, the pipeline stage, and the doctor all
    pull ``master.workspace_root`` (and the W7/W8 keys later) through it,
    and it stays safe on non-dict ``master`` trees (a hand-edited config
    that made ``master`` a string reads as empty).
    """
    node = (cfg or {}).get("master")
    return cast(dict[str, Any], node) if isinstance(node, dict) else {}


def slug_from_path(path: str, workspace_root: str) -> str | None:
    """The first path segment under the workspace root, or None.

    An empty root is the OFF switch. The path must sit under the root
    after ``normcase``/``normpath`` normalization, and the segment must
    fully match ``[-a-zA-Z0-9]+`` (no dots, no underscores).
    """
    if not workspace_root:
        return None
    root = os.path.normcase(os.path.normpath(workspace_root))
    full = os.path.normcase(os.path.normpath(path))
    if full == root or not full.startswith(root + os.sep):
        return None
    rest = full[len(root) + len(os.sep) :]
    head = rest.split(os.sep, 1)[0]
    if not _SLUG_RE.fullmatch(head):
        return None
    return head

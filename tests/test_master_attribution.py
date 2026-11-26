"""Workspace attribution (W5-2): the slug_from_path contract."""

from __future__ import annotations

from corpus_kb.master.attribution import slug_from_path

ROOT = "F:/ws"


def test_slug_under_root() -> None:
    assert slug_from_path("F:/ws/myproj/x.txt", ROOT) == "myproj"
    # Deeper nesting still answers the first segment.
    assert slug_from_path("F:/ws/myproj/sub/y.md", ROOT) == "myproj"


def test_path_outside_root() -> None:
    assert slug_from_path("F:/other/x.txt", ROOT) is None
    # The root itself is not under a slug folder.
    assert slug_from_path("F:/ws", ROOT) is None


def test_empty_root_is_off() -> None:
    assert slug_from_path("F:/ws/myproj/x.txt", "") is None


def test_bad_slug_segment_rejected() -> None:
    assert slug_from_path("F:/ws/a_b/x.txt", ROOT) is None
    # ".." normalizes outside the root, so the segment test never sees it.
    assert slug_from_path("F:/ws/../x.txt", ROOT) is None

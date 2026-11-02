"""Cross-file tracing isolation guard (issue #282).

``tests/test_tracing.py`` installs a real SDK TracerProvider as the
process global. That install is once-per-process, so it survives the
file. Before the teardown reset existed, every later span in the same
pytest process recorded, envelopes grew a ``trace_id`` key, and the
envelope-parity gate failed on the query and verify_answer MCP legs.
This file sorts right after ``test_tracing.py`` and pins the post-file
state: tracing stays off for the rest of the process.

CI never installs the ``[otel]`` extra, so the SDK tier of the tracing
tests skips there and this guard skips with it — the pollution cannot
exist without the SDK.
"""

from __future__ import annotations

import pytest

from corpus_kb.utils.tracing import SPAN_QUERY, current_trace_id, span


def test_tracing_is_off_after_the_tracing_file() -> None:
    """A fresh span stays non-recording once the tracing file is done."""
    pytest.importorskip("opentelemetry.sdk")
    with span(SPAN_QUERY):
        assert current_trace_id() is None

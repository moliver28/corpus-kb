"""U24 deduction guard: settings reader, decision semantics, cache gate.

Offline: the DB read path is isolated behind ``active_release_for_conn``
(tested with a fake connection); the pure decision logic and the config
reader are tested directly.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb.config import get_default_config
from corpus_kb.research.deduction_guard import (
    RELEASED_LABEL,
    SANDBOX_LABEL,
    UNRELEASED_DRAFT_LABEL,
    ActiveRelease,
    active_release_from_row,
    decide_guard,
    release_guard_settings,
)
from corpus_kb.research.deductive_run import _cache_block
from corpus_kb.research.exact_cache import CacheSettings

TENANT = UUID("00000000-0000-0000-0000-000000000001")
VERSION = UUID("00000000-0000-0000-0000-000000000002")


def _released_release(**overrides: Any) -> ActiveRelease:
    fields: dict[str, Any] = {
        "release_id": str(uuid4()),
        "state": "released",
        "manifest_sha256": "a" * 64,
        "codebook_version_sha256": "b" * 64,
        "released_at": "2026-10-09T00:00:00+00:00",
        "profile": "team-codebook",
    }
    fields.update(overrides)
    return ActiveRelease(**fields)


class _Row(dict):
    """Minimal asyncpg.Record stand-in (str-keyed item access)."""


# ---------------------------------------------------------------------------
# release_guard_settings: the pre-staged config keys are LIVE
# ---------------------------------------------------------------------------


def test_settings_read_the_default_config() -> None:
    settings = release_guard_settings(dict(get_default_config()))
    assert settings.enforcement == "warn"
    assert settings.default_profile == "team-codebook"


def test_settings_read_overrides_fail_loud_on_bad_values() -> None:
    settings = release_guard_settings({"research": {"release": {"enforcement": "enforce"}}})
    assert settings.enforcement == "enforce"
    with pytest.raises(ValueError, match="enforcement"):
        release_guard_settings({"research": {"release": {"enforcement": "strict"}}})
    with pytest.raises(ValueError, match="must be a mapping"):
        release_guard_settings({"research": {"release": "warn"}})


# ---------------------------------------------------------------------------
# active release resolution (fake-conn read path)
# ---------------------------------------------------------------------------


def test_active_release_from_row_maps_and_handles_none() -> None:
    row = _Row(
        release_id="r-1",
        state="released",
        profile="team-codebook",
        manifest_sha256="a" * 64,
        codebook_version_sha256="b" * 64,
        released_at=None,
    )
    release = active_release_from_row(row)
    assert release is not None
    assert release.is_released is True
    assert release.released_at is None
    assert active_release_from_row(None) is None


class _FakeConn:
    def __init__(self, row: Any) -> None:
        self._row = row
        self.calls: list[tuple[object, ...]] = []

    async def fetchrow(self, sql: str, *args: object) -> Any:
        self.calls.append(args)
        return self._row


async def test_active_release_for_conn_queries_under_tenant_scope() -> None:
    from corpus_kb.research.deduction_guard import active_release_for_conn

    conn = _FakeConn(
        _Row(
            release_id="r-2",
            state="draft_candidate",
            profile="exploratory",
            manifest_sha256="c" * 64,
            codebook_version_sha256="d" * 64,
            released_at=None,
        )
    )
    release = await active_release_for_conn(conn, TENANT, VERSION)
    assert release is not None
    assert release.state == "draft_candidate"
    assert release.is_released is False
    assert conn.calls and conn.calls[0] == (TENANT, VERSION)


# ---------------------------------------------------------------------------
# decide_guard: off unchanged, warn warns, enforce blocks / sandbox bypass
# ---------------------------------------------------------------------------


def test_off_is_unchanged_no_label_no_warning() -> None:
    from corpus_kb.research.deduction_guard import ReleaseGuardSettings

    settings = ReleaseGuardSettings(enforcement="off")
    for active in (None, _released_release()):
        decision = decide_guard(active, settings, VERSION)
        assert decision.action == "run"
        assert decision.label == ""
        assert decision.warnings == ()


def test_warn_continues_with_released_hash_recorded() -> None:
    from corpus_kb.research.deduction_guard import ReleaseGuardSettings

    active = _released_release()
    decision = decide_guard(active, ReleaseGuardSettings(), VERSION)
    assert decision.action == "run"
    assert decision.label == RELEASED_LABEL
    block = decision.summary_block()
    assert block["manifest_sha256"] == active.manifest_sha256
    assert block["release_id"] == active.release_id
    assert block["warnings"] == []


def test_warn_continues_with_unreleased_draft_warning() -> None:
    from corpus_kb.research.deduction_guard import ReleaseGuardSettings

    decision = decide_guard(None, ReleaseGuardSettings(), VERSION)
    assert decision.action == "run"
    assert decision.label == UNRELEASED_DRAFT_LABEL
    assert any(UNRELEASED_DRAFT_LABEL in w for w in decision.warnings)
    draft_only = _released_release(state="draft_candidate")
    decision = decide_guard(draft_only, ReleaseGuardSettings(), VERSION)
    assert decision.action == "run"
    assert decision.label == UNRELEASED_DRAFT_LABEL
    assert "draft_candidate" in decision.reason


def test_enforce_blocks_unreleased_cleanly() -> None:
    from corpus_kb.research.deduction_guard import ReleaseGuardSettings

    settings = ReleaseGuardSettings(enforcement="enforce")
    blocked = decide_guard(None, settings, VERSION)
    assert blocked.action == "block"
    assert blocked.label == UNRELEASED_DRAFT_LABEL
    assert "no release exists" in blocked.reason
    superseded = _released_release(state="superseded")
    blocked = decide_guard(superseded, settings, VERSION)
    assert blocked.action == "block"
    assert "superseded" in blocked.reason
    released = decide_guard(_released_release(), settings, VERSION)
    assert released.action == "run"
    assert released.label == RELEASED_LABEL


def test_enforce_allow_draft_bypass_labels_sandbox() -> None:
    from corpus_kb.research.deduction_guard import ReleaseGuardSettings

    decision = decide_guard(
        None, ReleaseGuardSettings(enforcement="enforce"), VERSION, allow_draft=True
    )
    assert decision.action == "run"
    assert decision.label == SANDBOX_LABEL
    assert any(SANDBOX_LABEL in w for w in decision.warnings)
    block = decision.summary_block()
    assert block["label"] == SANDBOX_LABEL
    # A bypass records NO release hash: there is no released release.
    assert "manifest_sha256" not in block


# ---------------------------------------------------------------------------
# U42 cache gate recorded per deductive run
# ---------------------------------------------------------------------------


def test_cache_block_records_the_gate_decision() -> None:
    default = _cache_block(CacheSettings())
    assert default["enabled"] is True
    assert default["deductive_allowed"] is True
    assert default["consulted"] is False  # v2 engine makes no LLM calls

    inductive_only = _cache_block(CacheSettings(enabled=True, deductive_enabled=False))
    assert inductive_only["deductive_allowed"] is False
    disabled = _cache_block(CacheSettings(enabled=False))
    assert disabled["deductive_allowed"] is False

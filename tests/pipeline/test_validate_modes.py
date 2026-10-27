"""Wave 4 validate-mode semantics through run_pipeline (hermetic).

off = byte-identical envelope; advisory attaches the declared extra key;
enforce raises the typed ValidationError; calibrate records the report in
the ledger stages without blocking. Two advisory runs give the same JSON.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import corpus_kb.tools.ingest_common as ingest_common_module
from corpus_kb.errors import ValidationError
from corpus_kb.tools.ingest_common import run_pipeline

CONFIG: dict[str, Any] = {
    "graph": {"extract_entities": False},
    "embedding": {"provider": "pgml"},
    "ingest": {"chunking": {"enabled": False}},
}

TEXT = "alpha beta gamma. " * 8


class _Conn:
    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[Any]:
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        return None

    async def fetchval(self, sql: str, *args: object) -> Any:
        if "pg_try_advisory_lock" in sql:
            return True
        if "to_regclass" in sql:
            return "public.ingest_runs"
        if "information_schema.columns" in sql:
            return 2
        return 0

    def transaction(self) -> _Conn:
        return self

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _Pool:
    def acquire(self) -> _Pool:
        return self

    async def __aenter__(self) -> _Conn:
        return _Conn()

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def close(self) -> None:
        return None


class _StubStore:
    finished_stages: dict[str, Any] | None = None

    def __init__(self, pool: Any, tenant_id: str = "t") -> None:
        pass

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[Any]) -> set[str]:
        return set()

    async def store_vectors(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def store_entities(self, entities: list[Any]) -> int:
        return 0

    async def store_relations(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        return None

    async def finish_run(self, run_id: str, file_hash: str, stages: Any, errors: Any) -> None:
        type(self).finished_stages = dict(stages)

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


@pytest.fixture
def pipeline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_common_module, "PostgresIngestStore", _StubStore)


def _normalize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Strip the run-varying keys so two runs compare equal."""
    out = dict(envelope)
    out["run_id"] = "<run>"
    extraction = dict(out.get("extraction", {}))
    extraction["duration_ms"] = 0
    out["extraction"] = extraction
    return out


async def test_off_envelope_has_no_validation_key(pipeline_env: None) -> None:
    result = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool())
    assert "validation" not in result
    assert "dedup_dropped" not in result


async def test_off_envelope_matches_declared_ingest_contract(pipeline_env: None) -> None:
    from corpus_kb.contracts.envelopes import INGEST_ENVELOPE

    result = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool())
    assert set(INGEST_ENVELOPE) <= set(result)


async def test_off_is_byte_identical_to_advisory_except_extra_key(pipeline_env: None) -> None:
    off = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool())
    advisory = await run_pipeline(
        TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="advisory"
    )
    trimmed = {k: v for k, v in advisory.items() if k != "validation"}
    assert _normalize(off) == _normalize(trimmed)


async def test_advisory_attaches_validation_summary(pipeline_env: None) -> None:
    result = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="advisory")
    report = result["validation"]
    assert report["mode"] == "advisory"
    assert report["status"] == "ok"
    assert report["problem_count"] == 0


async def test_advisory_is_deterministic(pipeline_env: None) -> None:
    first = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="advisory")
    second = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="advisory")
    assert json.dumps(first["validation"], sort_keys=True) == json.dumps(
        second["validation"], sort_keys=True
    )


async def test_enforce_raises_typed_error_on_overlapping_chunks(
    pipeline_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enforce fails closed on a conformance-valid plan with overlap."""
    from corpus_kb.ingestion import registry
    from corpus_kb.ingestion.base import (
        ChunkPlan,
        DetectResult,
        PlannedChunk,
        SourceProfile,
    )

    class _OverlappingStrategy:
        name = "parity-overlap"

        def detect(self, hint: Any, filename: str, head: str) -> DetectResult:
            return DetectResult(0.0, "none")

        def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
            mid = len(raw) // 2
            return ChunkPlan(
                strategy=self.name,
                document_text=raw,
                chunks=[
                    PlannedChunk(
                        text=raw[: mid + 10],
                        index=0,
                        source_start_char=0,
                        source_end_char=mid + 10,
                    ),
                    PlannedChunk(
                        text=raw[mid:], index=1, source_start_char=mid, source_end_char=len(raw)
                    ),
                ],
            )

    monkeypatch.setattr(registry, "register_strategy", registry.register_strategy)
    registry.register_strategy(_OverlappingStrategy())
    try:
        overlap_config = dict(CONFIG)
        overlap_config["ingestion"] = dict(
            CONFIG.get("ingestion", {}), strategies={"text": "parity-overlap"}
        )
        with pytest.raises(ValidationError):
            await run_pipeline(TEXT, "text", "s", overlap_config, _Pool(), validate_mode="enforce")
    finally:
        registry._REGISTRY.pop("parity-overlap", None)


async def test_enforce_passes_clean_document(pipeline_env: None) -> None:
    result = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="enforce")
    assert result["status"] == "success"


async def test_invalid_mode_raises_typed_error(pipeline_env: None) -> None:
    with pytest.raises(ValidationError):
        await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="strict")


async def test_calibrate_records_ledger_stage_not_envelope(pipeline_env: None) -> None:
    _StubStore.finished_stages = None
    result = await run_pipeline(TEXT, "text", "s", dict(CONFIG), _Pool(), validate_mode="calibrate")
    assert "validation" not in result
    stages = _StubStore.finished_stages
    assert stages is not None
    assert stages["validation"]["mode"] == "calibrate"
    assert stages["validation"]["status"] == "ok"


async def test_domain_command_threads_validate_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP/socket construct IngestTextCommand; validate must reach the run."""
    from uuid import UUID

    import corpus_kb.handlers.command_handler as command_handler_module
    import corpus_kb.handlers.delete_audit as delete_audit_module
    from corpus_kb.domain.models import IngestTextCommand

    class _FakeApp:
        def save(self, aggregate: Any) -> None:
            return None

    monkeypatch.setattr(delete_audit_module, "get_app_for_config", lambda _cfg: _FakeApp())
    monkeypatch.setattr(command_handler_module, "get_app_for_config", lambda _cfg: _FakeApp())
    command_handler_module.reset_command_handler()
    try:
        handler = command_handler_module.get_command_handler(config=dict(CONFIG), pool=_Pool())
        result = await handler.handle_ingest_text(
            IngestTextCommand(
                tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
                text=TEXT,
                source="s",
                source_type="text",
                validate_mode="advisory",
            )
        )
        assert result["validation"]["mode"] == "advisory"
    finally:
        command_handler_module.reset_command_handler()


# -- Profile validate default (unified-ingestion Wave 6): an unset caller
# -- mode falls back to the profile's ``validate`` value; an explicit mode wins.


def _profile_config(tmp_path: Any, validate: str) -> dict[str, Any]:
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "aud.yaml").write_text(
        f'name: aud\ndata_type: product\nvalidate: "{validate}"\n', encoding="utf-8"
    )
    config = dict(CONFIG)
    config["profiles"] = {"dir": str(profiles)}
    config["ingestion"] = {"profile": "aud"}
    return config


async def test_profile_validate_applies_when_caller_omits_mode(
    pipeline_env: None, tmp_path: Any
) -> None:
    result = await run_pipeline(TEXT, "text", "s", _profile_config(tmp_path, "advisory"), _Pool())
    assert result["validation"]["mode"] == "advisory"


async def test_explicit_mode_beats_profile(pipeline_env: None, tmp_path: Any) -> None:
    config = _profile_config(tmp_path, "advisory")
    result = await run_pipeline(TEXT, "text", "s", config, _Pool(), validate_mode="off")
    assert "validation" not in result


async def test_profile_off_keeps_envelope_clean(pipeline_env: None, tmp_path: Any) -> None:
    result = await run_pipeline(TEXT, "text", "s", _profile_config(tmp_path, "off"), _Pool())
    assert "validation" not in result


def _command_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A CommandHandler whose aggregate seam is stubbed (no DB writes)."""
    import corpus_kb.handlers.command_handler as command_handler_module
    import corpus_kb.handlers.delete_audit as delete_audit_module

    class _FakeApp:
        def save(self, aggregate: Any) -> None:
            return None

    monkeypatch.setattr(delete_audit_module, "get_app_for_config", lambda _cfg: _FakeApp())
    monkeypatch.setattr(command_handler_module, "get_app_for_config", lambda _cfg: _FakeApp())
    command_handler_module.reset_command_handler()
    return command_handler_module


async def test_command_omitted_validate_uses_profile_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """An omitted validate on the command rides the profile default (Wave 6)."""
    from uuid import UUID

    from corpus_kb.domain.models import IngestTextCommand

    module = _command_env(monkeypatch)
    try:
        handler = module.get_command_handler(
            config=_profile_config(tmp_path, "advisory"), pool=_Pool()
        )
        result = await handler.handle_ingest_text(
            IngestTextCommand(
                tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
                text=TEXT,
                source="s",
                source_type="text",
            )
        )
        assert result["validation"]["mode"] == "advisory"
    finally:
        module.reset_command_handler()


async def test_command_explicit_off_beats_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """An explicit off on the command wins over the profile default."""
    from uuid import UUID

    from corpus_kb.domain.models import IngestTextCommand

    module = _command_env(monkeypatch)
    try:
        handler = module.get_command_handler(
            config=_profile_config(tmp_path, "advisory"), pool=_Pool()
        )
        result = await handler.handle_ingest_text(
            IngestTextCommand(
                tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
                text=TEXT,
                source="s",
                source_type="text",
                validate_mode="off",
            )
        )
        assert "validation" not in result
    finally:
        module.reset_command_handler()


async def test_command_omitted_without_profile_stays_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No profile plus an omitted validate keeps the envelope clean."""
    from uuid import UUID

    from corpus_kb.domain.models import IngestTextCommand

    module = _command_env(monkeypatch)
    try:
        handler = module.get_command_handler(config=dict(CONFIG), pool=_Pool())
        result = await handler.handle_ingest_text(
            IngestTextCommand(
                tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
                text=TEXT,
                source="s",
                source_type="text",
            )
        )
        assert "validation" not in result
    finally:
        module.reset_command_handler()

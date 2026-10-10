"""Offline tests for the doctor's pgvector version check + zero-vector canary.

Pure-function contract tests (no Postgres, no Ollama): the network-touching
paths are stubbed at ``doctor_research._embed_probe``.
"""

from __future__ import annotations

from corpus_kb._setup import doctor_research as dr


def test_pgvector_version_ok_on_08_plus() -> None:
    check = dr.check_pgvector_version("0.8.2")
    assert check.status == dr.STATUS_OK
    assert "hnsw.iterative_scan" in check.detail
    assert "not_enforced" not in check.detail


def test_pgvector_version_warns_not_enforced_below_080() -> None:
    check = dr.check_pgvector_version("0.7.4")
    assert check.status == dr.STATUS_WARN
    assert "not_enforced" in check.detail
    assert "0.7.4" in check.detail
    assert check.fix


def test_pgvector_version_warns_not_enforced_on_unreadable_version() -> None:
    check = dr.check_pgvector_version("not-a-version")
    assert check.status == dr.STATUS_WARN
    assert "not_enforced" in check.detail


def test_pgvector_missing_extension_fails() -> None:
    check = dr.check_pgvector_version(None)
    assert check.status == dr.STATUS_FAIL
    assert "not installed" in check.detail


def _canary_config() -> dict[str, object]:
    return {"embedding": {"provider": "ollama", "model": "qwen3-embedding:8b-q8_0"}}


def test_canary_skips_not_evaluable_when_ollama_unreachable() -> None:
    check = dr.zero_vector_canary(_canary_config(), ollama_ok=False)
    assert check.status == dr.STATUS_SKIPPED
    assert "not_evaluable" in check.detail
    assert check.fix


def test_canary_skips_not_evaluable_when_reachability_unknown() -> None:
    check = dr.zero_vector_canary(_canary_config(), ollama_ok=None)
    assert check.status == dr.STATUS_SKIPPED
    assert "not_evaluable" in check.detail


def test_canary_fails_invalid_output_on_all_zero_vector(monkeypatch) -> None:
    def _zeros(config: dict[str, object]) -> list[float]:
        return [0.0] * 8

    monkeypatch.setattr(dr, "_embed_probe", _zeros)
    check = dr.zero_vector_canary(_canary_config(), ollama_ok=True)
    assert check.status == dr.STATUS_FAIL
    assert "invalid_output" in check.detail
    assert "qwen3-embedding:8b-q8_0" in check.detail
    assert check.fix


def test_canary_passes_on_non_zero_vector(monkeypatch) -> None:
    def _real(config: dict[str, object]) -> list[float]:
        return [0.5] * 8

    monkeypatch.setattr(dr, "_embed_probe", _real)
    check = dr.zero_vector_canary(_canary_config(), ollama_ok=True)
    assert check.status == dr.STATUS_OK
    assert "non-zero" in check.detail
    assert "dims=8" in check.detail


def test_canary_degrades_to_not_evaluable_on_probe_error(monkeypatch) -> None:
    def _boom(config: dict[str, object]) -> list[float]:
        raise RuntimeError("model not pulled")

    monkeypatch.setattr(dr, "_embed_probe", _boom)
    check = dr.zero_vector_canary(_canary_config(), ollama_ok=True)
    assert check.status == dr.STATUS_SKIPPED
    assert "not_evaluable" in check.detail
    assert "model not pulled" in check.detail


def test_probe_text_is_fixed_and_stable() -> None:
    from corpus_kb.rag.fake_embedder import PROBE_TEXT

    assert PROBE_TEXT
    assert dr._embed_probe.__doc__  # the probe path documents itself
    # The canary and the eval oracle share ONE canonical probe string.
    assert "canary" in PROBE_TEXT

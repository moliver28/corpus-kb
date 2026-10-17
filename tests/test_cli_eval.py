"""The ``corpus-kb eval`` CLI leg: honest exits (OFFLINE).

The pool, the runner seam and the config loader are faked at the same
seams the other lifecycle commands use, so exit codes, the human
rendering, and the ``--json`` envelope are exercised end-to-end without
a database, Ollama, or deepeval.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.errors import CorpusError
from corpus_kb.eval.golden import GoldenRecord
from corpus_kb.eval.report import EvalReport, RetrievalRow, RetrievalSummary

runner = CliRunner()

_CFG: dict[str, object] = {"embedding": {"model": "nomic-embed-text", "dimensions": 1024}}


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _report(status: str = "success") -> EvalReport:
    return EvalReport(
        status=status,
        golden={"records": 1, "with_answer": 0, "without_answer": 1},
        retrieval=RetrievalSummary(
            recall_at_k=0.5,
            mrr=0.5,
            ndcg_at_10=0.63,
            k=2,
            n_records=1,
            p95_latency_ms=1.0,
            per_record=[
                RetrievalRow(
                    query="billing concern",
                    relevant_count=1,
                    retrieved_count=2,
                    recall_at_k=0.5,
                    mrr=0.5,
                    ndcg_at_10=0.63,
                    latency_ms=1.0,
                )
            ],
        ),
        answer={"skipped": "answer metrics disabled by --skip-answer-metrics"},
        degraded_excluded={"count": 0, "reasons": []},
    )


def _patch_world(
    monkeypatch: pytest.MonkeyPatch,
    report: EvalReport | None = None,
    error: Exception | None = None,
) -> tuple[_FakePool, dict[str, object]]:
    pool = _FakePool()
    captured: dict[str, object] = {}

    async def _open(_cfg: dict[str, object]) -> _FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return dict(_CFG)

    async def _fake_run_eval(*args: object, **kwargs: object) -> EvalReport:
        captured["args"] = args
        captured.update(kwargs)
        if error is not None:
            raise error
        return report if report is not None else _report()

    monkeypatch.setattr("corpus_kb.cli_eval._load_config", _cfg)
    monkeypatch.setattr("corpus_kb.cli_eval._open_pool", _open)
    monkeypatch.setattr("corpus_kb.eval.runner.run_eval", _fake_run_eval)
    return pool, captured


def _golden_file(tmp_path: Path) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"query": "q", "relevant_chunk_ids":'
        ' ["00000000-0000-0000-0000-000000000001"], "answer": null}\n',
        encoding="utf-8",
    )
    return path


def test_run_json_emits_the_exact_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from corpus_kb.eval.report import to_envelope

    report = _report()
    pool, _ = _patch_world(monkeypatch, report)
    result = runner.invoke(app, ["eval", "run", "--golden", str(_golden_file(tmp_path)), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == to_envelope(report)
    assert pool.closed is True


def test_run_passes_flags_to_the_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool, captured = _patch_world(monkeypatch)
    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--golden",
            str(_golden_file(tmp_path)),
            "--k",
            "5",
            "--skip-answer-metrics",
            "--project",
            "00000000-0000-0000-0000-000000000009",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["k"] == 5
    assert captured["skip_answer_metrics"] is True
    assert captured["project_id"] == "00000000-0000-0000-0000-000000000009"
    records = captured["args"][0]
    assert isinstance(records, list) and records
    assert pool.closed is True


def test_run_defaults_project_to_the_nil_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corpus_kb.eval.runner import NIL_PROJECT_ID

    _, captured = _patch_world(monkeypatch)
    result = runner.invoke(app, ["eval", "run", "--golden", str(_golden_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert captured["project_id"] == NIL_PROJECT_ID


def test_run_human_output_shows_the_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_world(monkeypatch)
    result = runner.invoke(app, ["eval", "run", "--golden", str(_golden_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "eval status: success" in result.output
    assert "recall@k=0.5" in result.output
    assert "answer: skipped (answer metrics disabled by --skip-answer-metrics)" in result.output


def test_run_writes_the_report_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_world(monkeypatch)
    report_path = tmp_path / "out.json"
    result = runner.invoke(
        app,
        ["eval", "run", "--golden", str(_golden_file(tmp_path)), "--report", str(report_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"


def test_run_malformed_golden_exits_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_world(monkeypatch)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    result = runner.invoke(app, ["eval", "run", "--golden", str(bad)])
    assert result.exit_code == 1
    assert "golden line 1" in result.output


def test_run_runner_failure_exits_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_world(monkeypatch, error=CorpusError("the pool exploded"))
    result = runner.invoke(app, ["eval", "run", "--golden", str(_golden_file(tmp_path))])
    assert result.exit_code == 1


def test_build_golden_validate_happy_path(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", "build-golden", "--validate", str(_golden_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "records: 1" in result.output
    assert "without answer: 1" in result.output


def test_build_golden_validate_malformed_exits_one(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"query": "q"}\n', encoding="utf-8")
    result = runner.invoke(app, ["eval", "build-golden", "--validate", str(bad)])
    assert result.exit_code == 1
    assert "golden line 1" in result.output


def test_build_golden_requires_exactly_one_mode() -> None:
    result = runner.invoke(app, ["eval", "build-golden"])
    assert result.exit_code == 1
    assert "choose exactly one" in result.output


def test_build_golden_from_assignments_needs_project() -> None:
    result = runner.invoke(app, ["eval", "build-golden", "--from-assignments"])
    assert result.exit_code == 1
    assert "--project" in result.output


def test_build_golden_from_assignments_writes_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from corpus_kb.research.retrieval import _child_uuid

    class _Conn:
        async def execute(self, sql: str, *args: object) -> str:
            return "OK"

        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            assert "'confirmed'" in sql and "'overridden'" in sql
            return [
                {
                    "code_id": "c1",
                    "name": "Billing Concern",
                    "brief_definition": "Mentions invoicing.",
                    "unit_ids": [3],
                }
            ]

        async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
            return None

        def transaction(self):
            outer = self

            class _Tx:
                async def __aenter__(self) -> _Conn:
                    return outer

                async def __aexit__(self, *exc: object) -> bool:
                    return False

            return _Tx()

    class _Pool:
        async def __aenter__(self) -> _Pool:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def acquire(self):
            class _Acquire:
                async def __aenter__(self) -> _Conn:
                    return _Conn()

                async def __aexit__(self, *exc: object) -> bool:
                    return False

            return _Acquire()

        async def close(self) -> None:
            return None

    monkeypatch.setattr("corpus_kb.cli_eval._load_config", lambda: dict(_CFG))

    async def _open(_cfg: dict[str, object]) -> _Pool:
        return _Pool()

    monkeypatch.setattr("corpus_kb.cli_eval._open_pool", _open)
    out = tmp_path / "built.jsonl"
    result = runner.invoke(
        app,
        [
            "eval",
            "build-golden",
            "--from-assignments",
            "--project",
            "00000000-0000-0000-0000-000000000009",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert records == [
        {
            "query": "Billing Concern. Mentions invoicing.",
            "relevant_chunk_ids": [_child_uuid("unit:3")],
            "answer": None,
        }
    ]


def test_golden_record_roundtrip_shape() -> None:
    record = GoldenRecord("q", ("00000000-0000-0000-0000-000000000001",), None)
    assert record.query == "q"
    assert record.answer is None

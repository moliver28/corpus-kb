"""Behavior tests for the codify command group (issue #354; 0.6.0 pinned).

Covers: the paradigm gate refuses before any network call, each command
sends the right op with the right arguments (the op runner is a mock),
cells files accept both shapes, ``--bands`` accepts only high/mid/low,
the cells builder caps candidates at five per cell, export prints CSV,
and ``codify provision`` stores the paradigm in the study registry.
No test starts a server or a database: HTTP seams are replaced.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import codify_commands  # noqa: E402
import codify_common  # noqa: E402
import op_client  # noqa: E402
import study_admin  # noqa: E402
import study_commands  # noqa: E402
import study_registry  # noqa: E402
import study_server  # noqa: E402


@pytest.fixture(autouse=True)
def plugin_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CORPUS_KB_STATE_DIR", str(tmp_path / "engine-state"))
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SUPERUSER_DSN", raising=False)
    return tmp_path


class Args:
    def __init__(self, **kwargs: object) -> None:
        self.study_slug = None
        for key, value in kwargs.items():
            setattr(self, key, value)


STUDY = {"port": 8010, "database_url": "x", "backend": "postgres"}


def _registry_with_paradigms() -> dict:
    """alpha=codebook, beta=reflexive, gamma=unrecorded; alpha active."""
    registry = study_registry.empty_registry()
    for slug, paradigm in (
        ("alpha", "codebook"),
        ("beta", "reflexive"),
        ("gamma", None),
    ):
        study_registry.register_study(
            registry, slug, f"postgresql://x/corpus_kb_{slug}", 8010, "postgres",
            paradigm=paradigm,
        )
    registry["active"] = "alpha"
    study_registry.save_registry(registry)
    return registry


def _scoped(monkeypatch: pytest.MonkeyPatch, slug: str = "alpha") -> list[tuple]:
    """Pin scoped_study and return the list of slugs it was asked for."""
    calls: list[tuple] = []

    def fake(slug_arg, ensure_server=True):
        calls.append((slug_arg, ensure_server))
        return "corpus-kb", slug, dict(STUDY)

    monkeypatch.setattr(study_commands, "scoped_study", fake)
    return calls


def _capture_op(monkeypatch: pytest.MonkeyPatch, envelope: dict | None = None) -> dict:
    seen: dict = {}

    def fake_run_op(cli, name, pairs, base_url, json_blob=None, **kw):
        seen.update(
            cli=cli, op=name, pairs=list(pairs), base_url=base_url,
            json_blob=json_blob, called=True,
        )
        return envelope if envelope is not None else {"status": "success"}

    monkeypatch.setattr(codify_common.op_runner, "run_op", fake_run_op)
    return seen


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **kw):
        raise AssertionError("a server call happened before the paradigm gate")

    monkeypatch.setattr(op_client, "request_json", boom)
    monkeypatch.setattr(op_client, "post_json", boom)
    monkeypatch.setattr(study_server, "ensure_running", boom)
    monkeypatch.setattr(study_server, "probe", boom)


# ---------------------------------------------------------------------------
# Paradigm gate: refuses before any server call
# ---------------------------------------------------------------------------


def test_reliability_refuses_for_reflexive_before_any_server_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _forbid_network(monkeypatch)
    code = codify_commands.cmd_codify(Args(codify_command="reliability", study_slug="beta"))
    out = capsys.readouterr().out
    assert code == 1
    assert "not ok" in out
    assert "reflexive" in out
    assert "inter-coder reliability" in out.replace("inter-coder\n", "inter-coder ")


def test_reliability_refuses_when_no_paradigm_is_on_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _forbid_network(monkeypatch)
    code = codify_commands.cmd_codify(Args(codify_command="reliability", study_slug="gamma"))
    out = capsys.readouterr().out
    assert code == 1
    assert "no paradigm on record" in out
    assert "codify provision" in out


def test_reliability_runs_for_codebook_study(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch, "alpha")
    seen = _capture_op(
        monkeypatch,
        {"status": "success", "overall": {"krippendorff_alpha": 0.82}, "per_code": []},
    )
    code = codify_commands.cmd_codify(
        Args(codify_command="reliability", study_slug="alpha", per_code_min=7, seed=3)
    )
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_reliability"
    assert "per_code_min=7" in seen["pairs"]
    assert "seed=3" in seen["pairs"]
    assert "0.82" in out
    assert "caution" in out.lower()  # the caution prints beside the alpha


# ---------------------------------------------------------------------------
# Each command sends the right op and arguments
# ---------------------------------------------------------------------------


def test_load_codebook_sends_file_json_and_prints_version_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    book = {"codes": [{"code_id": "k1", "name": "Trust"}]}
    path = tmp_path / "codebook.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    seen = _capture_op(
        monkeypatch, {"status": "success", "codebook_version_id": "v-123", "codes_embedded": 1}
    )
    code = codify_commands.cmd_codify(Args(codify_command="load-codebook", file=str(path)))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_embed_codebook"
    assert seen["json_blob"] == json.dumps(book)
    assert not seen["pairs"]
    assert "v-123" in out
    assert "version" in out.lower()


def test_pool_uses_the_given_version_and_prints_governance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(
        monkeypatch,
        {"status": "success", "keyword_governance": {"collisions": 1}, "speaker_roles": {}},
    )
    code = codify_commands.cmd_codify(
        Args(codify_command="pool", study_slug="alpha", codebook_version="v-9")
    )
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_pool"
    assert "codebook_version_id=v-9" in seen["pairs"]
    assert "collisions" in out
    assert "speaker_roles" in out


def test_pool_defaults_to_the_latest_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(monkeypatch, {"status": "success"})

    def fake_sql(url, payload, timeout=120):
        assert "codebook_versions" in payload["sql"]
        return {"status": "success", "result": [{"version_id": "v-latest"}]}

    monkeypatch.setattr(op_client, "post_json", fake_sql)
    code = codify_commands.cmd_codify(
        Args(codify_command="pool", study_slug="alpha", codebook_version=None)
    )
    assert code == 0
    assert "codebook_version_id=v-latest" in seen["pairs"]


def test_code_sends_cells_coder_and_optional_args(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    cells = [{"chunk_id": "c1", "code_ids": ["k1"]}]
    path = tmp_path / "cells.json"
    path.write_text(json.dumps({"cells": cells}), encoding="utf-8")
    seen = _capture_op(
        monkeypatch,
        {"status": "success", "cells_coded": 1, "cells_rejected": 0, "cells_routed": {}},
    )
    code = codify_commands.cmd_codify(Args(
        codify_command="code", cells=str(path), coder="gpt-x", model="m1",
        batch_id="b1", coding_pass=2, bands='{"high": 0.95, "mid": 0.6, "low": 0.4}',
    ))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_code_batch"
    pairs = dict(pair.split("=", 1) for pair in seen["pairs"])
    assert json.loads(pairs["cells"]) == cells
    assert pairs["coder"] == "gpt-x"
    assert pairs["model"] == "m1"
    assert pairs["batch_id"] == "b1"
    assert pairs["coding_pass"] == "2"
    assert json.loads(pairs["bands"]) == {"high": 0.95, "mid": 0.6, "low": 0.4}
    assert "1 coded" in out


def test_code_accepts_a_bare_list_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    cells = [{"chunk_id": "c2", "code_ids": ["k2", "k3"]}]
    path = tmp_path / "cells.json"
    path.write_text(json.dumps(cells), encoding="utf-8")
    seen = _capture_op(monkeypatch, {"status": "success", "cells_coded": 1})
    code = codify_commands.cmd_codify(Args(
        codify_command="code", cells=str(path), coder=None, model=None,
        batch_id=None, coding_pass=None, bands=None,
    ))
    assert code == 0
    pairs = dict(pair.split("=", 1) for pair in seen["pairs"])
    assert json.loads(pairs["cells"]) == cells
    assert "coder" not in pairs  # omitted flags are not sent


def test_code_refuses_a_bad_cells_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(monkeypatch)
    path = tmp_path / "cells.json"
    path.write_text('{"other": []}', encoding="utf-8")
    code = codify_commands.cmd_codify(Args(
        codify_command="code", cells=str(path), coder=None, model=None,
        batch_id=None, coding_pass=None, bands=None,
    ))
    out = capsys.readouterr().out
    assert code == 1
    assert "not ok" in out
    assert seen.get("called") is None


# ---------------------------------------------------------------------------
# --bands accepts only high, mid, low
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ['{"high": 0.9, "bogus": 1}', "[1]", '{"high": "x"}', "{broken"])
def test_bands_refuses_bad_shapes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path, raw: str
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(monkeypatch)
    path = tmp_path / "cells.json"
    path.write_text("[]", encoding="utf-8")
    code = codify_commands.cmd_codify(Args(
        codify_command="code", cells=str(path), coder=None, model=None,
        batch_id=None, coding_pass=None, bands=raw,
    ))
    out = capsys.readouterr().out
    assert code == 1
    assert "not ok" in out
    assert "high, mid, low" in out or "JSON object" in out
    assert seen.get("called") is None


# ---------------------------------------------------------------------------
# calibrate-floors, saturation, status, export
# ---------------------------------------------------------------------------


def test_calibrate_floors_sends_labels_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    labels = {
        "code_id": "k1",
        "codebook_version_id": "v-1",
        "positive_chunk_ids": ["c1", "c2"],
        "negative_chunk_ids": ["c3"],
    }
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels), encoding="utf-8")
    seen = _capture_op(
        monkeypatch, {"status": "success", "pool_floor": 0.3, "residual_floor": 0.1}
    )
    code = codify_commands.cmd_codify(Args(codify_command="calibrate-floors", labels=str(path)))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_calibrate_floors"
    assert seen["json_blob"] == json.dumps(labels)
    assert "0.3" in out


def test_saturation_sends_the_stop_rule_args(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(
        monkeypatch, {"status": "success", "isr": 0.02, "should_stop": True, "new_codes": 1}
    )
    code = codify_commands.cmd_codify(Args(
        codify_command="saturation", study_slug="alpha", batch_id="b1",
        threshold=0.02, min_samples=25,
    ))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_saturation"
    assert "batch_id=b1" in seen["pairs"]
    assert "threshold=0.02" in seen["pairs"]
    assert "min_samples=25" in seen["pairs"]
    assert "should_stop" in out


def test_status_sends_the_coding_status_op(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    seen = _capture_op(
        monkeypatch,
        {"status": "success", "documents": 4, "chunks": 40, "saturation": {}, "coverage": {}},
    )
    code = codify_commands.cmd_codify(Args(codify_command="status", study_slug="alpha"))
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_status"
    assert seen["pairs"] == []
    assert "chunks" in out


def test_export_json_prints_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    rows = {
        "status": "success",
        "columns": ["chunk_id", "text"],
        "rows": [{"chunk_id": "c", "text": "t"}],
    }
    seen = _capture_op(monkeypatch, rows)
    code = codify_commands.cmd_codify(
        Args(codify_command="export", study_slug="alpha", format="json")
    )
    out = capsys.readouterr().out
    assert code == 0
    assert seen["op"] == "coding_export"
    assert '"chunk_id"' in out


def test_export_csv_prints_a_csv_document(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    envelope = {
        "status": "success",
        "columns": ["chunk_id", "text"],
        "rows": [{"chunk_id": "c1", "text": "a,b"}],
    }
    _capture_op(monkeypatch, envelope)
    code = codify_commands.cmd_codify(
        Args(codify_command="export", study_slug="alpha", format="csv")
    )
    out = capsys.readouterr().out
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "chunk_id,text"
    assert lines[1] == 'c1,"a,b"'


# ---------------------------------------------------------------------------
# codify provision stores the paradigm
# ---------------------------------------------------------------------------


def test_provision_records_paradigm_for_a_new_study(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_provision(args):
        registry = study_registry.load_registry()
        study_registry.register_study(
            registry, "delta", "postgresql://x/corpus_kb_delta", 8011, "duckdb"
        )
        study_registry.save_registry(registry)
        return 0

    monkeypatch.setattr(study_admin, "_provision_study", fake_provision)
    code = codify_commands.cmd_codify(
        Args(codify_command="provision", slug="delta", paradigm="codebook", backend="duckdb")
    )
    out = capsys.readouterr().out
    assert code == 0
    registry = study_registry.load_registry()
    assert registry["studies"]["delta"]["paradigm"] == "codebook"
    assert "codebook" in out


def test_provision_records_paradigm_on_an_existing_study_without_reprovisioning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry_with_paradigms()

    def boom(args):
        raise AssertionError("an existing study must not be provisioned again")

    monkeypatch.setattr(study_admin, "_provision_study", boom)
    code = codify_commands.cmd_codify(
        Args(codify_command="provision", slug="alpha", paradigm="reflexive", backend=None)
    )
    assert code == 0
    registry = study_registry.load_registry()
    assert registry["studies"]["alpha"]["paradigm"] == "reflexive"


def test_codify_provision_records_the_paradigm_through_the_locked_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry_with_paradigms()
    seen: dict = {}
    real = study_registry.mutate_registry

    def spy(mutate):
        seen["called"] = True
        return real(mutate)

    monkeypatch.setattr(study_registry, "mutate_registry", spy)
    code = codify_commands.cmd_codify(
        Args(codify_command="provision", slug="alpha", paradigm="codebook", backend=None)
    )
    assert code == 0
    assert seen["called"] is True
    assert study_registry.load_registry()["studies"]["alpha"]["paradigm"] == "codebook"


def test_provision_refuses_an_unknown_paradigm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = codify_commands.cmd_codify(
        Args(codify_command="provision", slug="alpha", paradigm="grounded", backend=None)
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "codebook or reflexive" in out


def test_provision_failure_leaves_no_paradigm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(study_admin, "_provision_study", lambda args: 1)
    code = codify_commands.cmd_codify(
        Args(codify_command="provision", slug="delta", paradigm="codebook", backend=None)
    )
    assert code == 1
    registry = study_registry.load_registry()
    assert "delta" not in registry.get("studies", {})


# ---------------------------------------------------------------------------
# codify cells: the op-free cells builder
# ---------------------------------------------------------------------------


def _cells_sql_mock(monkeypatch: pytest.MonkeyPatch, candidates: list[dict], unpooled: int) -> None:
    def fake_sql(url, payload, timeout=120):
        sql = payload["sql"]
        if "codebook_versions" in sql:
            return {"status": "success", "result": [{"version_id": "v-latest"}]}
        if "chunk_signals" in sql and "GROUP BY" in sql:
            return {"status": "success", "result": candidates}
        if "COUNT" in sql:
            return {"status": "success", "result": [{"n": unpooled}]}
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(op_client, "post_json", fake_sql)


def test_cells_builds_top_five_candidates_per_chunk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    candidates = [
        {"chunk_id": f"c{i}", "code_id": f"k{j}", "top_score": 0.9 - j * 0.1}
        for i in range(1, 4)
        for j in range(6)  # six candidates: only five may survive
    ]
    _cells_sql_mock(monkeypatch, candidates, unpooled=2)
    out_path = tmp_path / "next.json"
    code = codify_commands.cmd_codify(Args(
        codify_command="cells", study_slug="alpha", coder="gpt-x", limit=None, out=str(out_path),
    ))
    out = capsys.readouterr().out
    assert code == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(doc["cells"]) == 3
    for cell in doc["cells"]:
        assert len(cell["code_ids"]) == 5  # at most five candidate codes per cell
        assert cell["code_ids"] == ["k0", "k1", "k2", "k3", "k4"]  # best score first
    assert doc["coder"] == "gpt-x"
    assert doc["codebook_version_id"] == "v-latest"
    assert "2" in out and "pool" in out  # the unpooled count and the pool hint


def test_cells_limit_caps_the_chunk_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    candidates = [
        {"chunk_id": f"c{i}", "code_id": "k1", "top_score": 0.5} for i in range(10)
    ]
    _cells_sql_mock(monkeypatch, candidates, unpooled=0)
    out_path = tmp_path / "next.json"
    code = codify_commands.cmd_codify(Args(
        codify_command="cells", study_slug="alpha", coder="gpt-x", limit=4, out=str(out_path),
    ))
    assert code == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(doc["cells"]) == 4


def test_cells_with_no_codebook_is_honest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)

    def fake_sql(url, payload, timeout=120):
        return {"status": "success", "result": []}

    monkeypatch.setattr(op_client, "post_json", fake_sql)
    code = codify_commands.cmd_codify(Args(
        codify_command="cells", study_slug="alpha", coder="gpt-x", limit=None,
        out=str(tmp_path / "next.json"),
    ))
    out = capsys.readouterr().out
    assert code == 1
    assert "load-codebook" in out


def test_cells_refuses_a_coder_id_with_bad_characters(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    code = codify_commands.cmd_codify(Args(
        codify_command="cells", study_slug="alpha", coder="gpt'; DROP TABLE chunks",
        limit=None, out=str(tmp_path / "next.json"),
    ))
    out = capsys.readouterr().out
    assert code == 1
    assert "not ok" in out


def test_cells_with_no_signals_says_to_pool_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _registry_with_paradigms()
    _scoped(monkeypatch)
    _cells_sql_mock(monkeypatch, [], unpooled=9)
    out_path = tmp_path / "next.json"
    code = codify_commands.cmd_codify(Args(
        codify_command="cells", study_slug="alpha", coder="gpt-x", limit=None, out=str(out_path),
    ))
    out = capsys.readouterr().out
    assert code == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["cells"] == []
    assert "codify pool" in out

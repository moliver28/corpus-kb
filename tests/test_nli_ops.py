"""Unit tests for the NLI ops (nli_classify, contradictions_screen).

The judge seam (``nli_contract.judge_raw``) is monkeypatched to look up
RECORDED temp-0 replies in ``tests/fixtures/nli_recorded/`` — one
SHA256(model + NUL + prompt) keyed JSON file per judgment, captured
live against local Ollama (qwen3:4b; bare ``qwen3`` was not a pulled
tag on the recording machine). A prompt with no recorded fixture fails
the test, so template drift cannot pass silently.

No Postgres: the vector+text seam is faked. No network: the transport
error path is driven with a raised httpx.ConnectError.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

from corpus_kb.analysis import nli_contract as contract
from corpus_kb.coding.uncertainty import nli_client
from corpus_kb.errors import ValidationError
from corpus_kb.tools import nli_tools

_SRC = Path(__file__).resolve().parent.parent / "src" / "corpus_kb"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nli_recorded"

# The model name the fixtures were recorded under (not the handler's
# NLI_MODEL constant; the fake judge maps between them).
FIXTURE_MODEL = "qwen3:4b"

# ---------------------------------------------------------------------------
# AST lazy-import contract (the P0 crash class)


LAZY_MODULES = (
    "analysis/vectors.py",
    "analysis/nli_contract.py",
    "tools/vector_screen_tools.py",
    "tools/neighbor_tools.py",
    "tools/centroid_tools.py",
    "tools/cluster_tools.py",
    "tools/nli_tools.py",
)
_BLOCKED = ("ollama", "sklearn", "bertopic", "umap", "hdbscan", "torch", "threadpoolctl")


def _top_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("relative", LAZY_MODULES)
def test_optional_imports_are_function_local(relative: str) -> None:
    roots = {name.split(".")[0] for name in _top_level_imports(_SRC / relative)}
    assert not roots & set(_BLOCKED), f"{relative} imports at module level: {sorted(roots)}"


# ---------------------------------------------------------------------------
# Fixture-backed judge


def _fixture_reply(prompt: str) -> str:
    key = hashlib.sha256(f"{FIXTURE_MODEL}\x00{prompt}".encode()).hexdigest()
    path = _FIXTURES / f"{key}.json"
    if not path.exists():
        raise AssertionError(f"no recorded NLI fixture for prompt (template drift?): {prompt!r}")
    return str(json.loads(path.read_text(encoding="utf-8"))["reply"])


async def _fake_judge(prompt: str, model: str, base_url: str) -> str:
    assert model and base_url  # the handler always passes them
    return _fixture_reply(prompt)


def _patch_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract, "judge_raw", _fake_judge)


def _patch_space(
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[str, list[float]],
    texts: dict[str, str],
) -> None:
    from corpus_kb.analysis import vectors

    ids = sorted(rows)
    matrix = np.asarray([rows[i] for i in ids], dtype=np.float64) if rows else np.zeros((0, 0))
    space = vectors.VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=0)

    async def fake_load(pg_pool: object, tenant_id: str, config: object):
        return space, texts

    monkeypatch.setattr(nli_tools, "_load_space", fake_load)


TEXTS = {
    "t1": "The router drops its wifi connection every evening.",
    "t2": "The customer was charged twice for one order.",
    "t3": "The battery lasts two days on one charge.",
    "t4": "The invoice lists the wrong company name.",
}


# ---------------------------------------------------------------------------
# Locked pins


def test_hypothesis_template_is_locked() -> None:
    assert contract.NLI_HYPOTHESIS_TEMPLATE == "This text is about {label}."
    assert contract.build_hypothesis("weather") == "This text is about weather."


def test_verdict_scores_are_locked() -> None:
    assert contract.VERDICT_SCORES == {"ENTAIL": 2.0, "NEUTRAL": 1.0, "CONTRADICT": 0.0}


def test_parse_verdict_tolerates_think_blocks_and_takes_the_last() -> None:
    raw = "<think>maybe ENTAIL? no</think> Final answer: CONTRADICT."
    assert contract.parse_verdict(raw) == "CONTRADICT"
    assert contract.parse_verdict("no idea") is None


# ---------------------------------------------------------------------------
# nli_classify


async def test_nli_classify_predicts_from_recorded_judgments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_judge(monkeypatch)
    _patch_space(monkeypatch, {"t1": [1.0, 0.0], "t2": [0.0, 1.0]}, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["network", "billing", "hardware"])
    assert out["status"] == "success"
    assert out["model"] == nli_client.NLI_MODEL
    assert out["screened_count"] == 2
    rows = {row["chunk_id"]: row for row in out["predictions"]}
    # t2 (charged twice) is billing on its own (ENTAIL, score 2).
    assert rows["t2"]["label"] == "billing"
    assert rows["t2"]["entailment_score"] == 2.0
    # t1 (router) is ENTAIL for BOTH hardware and network: the tie goes
    # to the alphabetically first label, and the sorted-label iteration
    # order is the lock (labels arrived unsorted above).
    assert rows["t1"]["label"] == "hardware"
    assert rows["t1"]["scores"][0]["label"] == "hardware"
    assert rows["t1"]["scores"][0]["score"] == 2.0
    assert rows["t1"]["scores"][1]["label"] == "network"
    assert rows["t1"]["scores"][2]["label"] == "billing"


async def test_nli_classify_limit_bounds_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch)
    rows = {"t1": [1.0, 0.0], "t2": [0.0, 1.0], "t3": [1.0, 1.0], "t4": [1.0, 2.0]}
    _patch_space(monkeypatch, rows, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["billing"], limit=2)
    assert out["screened_count"] == 2
    assert [row["chunk_id"] for row in out["predictions"]] == ["t1", "t2"]


async def test_nli_classify_explicit_ids_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch)
    _patch_space(monkeypatch, {"t1": [1.0, 0.0], "t2": [0.0, 1.0]}, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["billing"], chunk_ids=["t2", "t1"])
    assert [row["chunk_id"] for row in out["predictions"]] == ["t1", "t2"]


async def test_nli_classify_empty_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_space(monkeypatch, {}, {})
    out = await nli_tools.nli_classify(None, None, "t", labels=["x"])
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["predictions"] == []
    assert out["model"] == nli_client.NLI_MODEL


async def test_nli_classify_unreachable_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(prompt: str, model: str, base_url: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(contract, "judge_raw", down)
    _patch_space(monkeypatch, {"t1": [1.0, 0.0]}, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["billing"])
    assert out["status"] == "unavailable"
    assert out["reason"] and out["remedy"]
    assert out["predictions"] == []
    # Default model path: the remedy names the override keyword.
    assert 'model="qwen3:4b"' in str(out["remedy"])


async def test_model_override_threads_through_the_judge_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def capture(prompt: str, model: str, base_url: str) -> str:
        seen.append(model)
        return "ENTAIL"

    monkeypatch.setattr(contract, "judge_raw", capture)
    _patch_space(monkeypatch, {"t1": [1.0, 0.0]}, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["billing"], model="qwen3:8b")
    assert out["status"] == "success"
    assert out["model"] == "qwen3:8b"  # the envelope names what ran
    assert seen == ["qwen3:8b"]  # the client seam received the override


async def test_nli_classify_unusable_reply_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def gibberish(prompt: str, model: str, base_url: str) -> str:
        return "42"

    monkeypatch.setattr(contract, "judge_raw", gibberish)
    _patch_space(monkeypatch, {"t1": [1.0, 0.0]}, TEXTS)
    out = await nli_tools.nli_classify(None, None, "t", labels=["billing"])
    assert out["status"] == "unavailable"
    assert "verdict" in str(out["reason"])


async def test_nli_classify_rejects_bad_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        await nli_tools.nli_classify(None, None, "t", labels=[])
    with pytest.raises(ValidationError):
        await nli_tools.nli_classify(None, None, "t", labels=["a", "a"])
    with pytest.raises(ValidationError):
        await nli_tools.nli_classify(None, None, "t", labels=["a"], limit=0)


# ---------------------------------------------------------------------------
# contradictions_screen


def _contradiction_corpus() -> tuple[dict[str, list[float]], dict[str, str]]:
    """Three mutual-nearest pairs; two true contradictions, one neutral.

    The vector geometry makes the candidate prefilter nominate EXACTLY
    the three pair edges (candidate_k=1), so the judge set is closed
    under the recorded fixtures — and provably not all 15 pairs.
    """
    rows = {
        "p1": [1.0, 0.0],
        "p2": [0.99, 0.01],
        "p3": [0.0, 1.0],
        "p4": [0.01, 0.99],
        "p5": [-1.0, 0.0],
        "p6": [-0.99, -0.01],
    }
    texts = {
        "p1": "The router drops its wifi connection every evening.",
        "p2": "The router keeps a stable wifi connection at all hours.",
        "p3": "The customer was charged twice for one order.",
        "p4": "The customer was charged once for the order.",
        "p5": "The battery lasts two days on one charge.",
        "p6": "Charging the battery takes two hours.",
    }
    return rows, texts


async def test_contradictions_screen_reports_marked_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_judge(monkeypatch)
    rows, texts = _contradiction_corpus()
    _patch_space(monkeypatch, rows, texts)
    out = await nli_tools.contradictions_screen(None, None, "t", candidate_k=1)
    assert out["status"] == "success"
    # Judged exactly the three pair edges — never all 15 pairs.
    assert out["screened_count"] == 3
    found = {(pair["chunk_id_i"], pair["chunk_id_j"]) for pair in out["pairs"]}
    assert found == {("p1", "p2"), ("p3", "p4")}
    for pair in out["pairs"]:
        assert pair["verdict"] == "CONTRADICT"
    ids = [(pair["chunk_id_i"], pair["chunk_id_j"]) for pair in out["pairs"]]
    assert ids == sorted(ids)  # deterministic pair order


async def test_contradictions_screen_empty_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_space(monkeypatch, {"solo": [1.0, 0.0]}, {"solo": "text"})
    out = await nli_tools.contradictions_screen(None, None, "t")
    assert out["status"] == "empty"
    assert out["reason"] and out["remedy"]
    assert out["screened_count"] == 1


async def test_contradictions_screen_unreachable_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def down(prompt: str, model: str, base_url: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(contract, "judge_raw", down)
    rows, texts = _contradiction_corpus()
    _patch_space(monkeypatch, rows, texts)
    out = await nli_tools.contradictions_screen(None, None, "t", candidate_k=1)
    assert out["status"] == "unavailable"
    assert out["reason"] and out["remedy"]
    assert out["pairs"] == []
    # Default model path: the remedy names the override keyword.
    assert 'model="qwen3:4b"' in str(out["remedy"])


async def test_contradictions_screen_rejects_bad_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        await nli_tools.contradictions_screen(None, None, "t", candidate_k=0)
    with pytest.raises(ValidationError):
        await nli_tools.contradictions_screen(None, None, "t", limit=0)


def test_every_recorded_fixture_carries_a_usable_reply() -> None:
    """The recorded set itself stays honest: parseable verdicts only."""
    for path in sorted(_FIXTURES.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["model"] == FIXTURE_MODEL
        assert contract.parse_verdict(record["reply"]) is not None, path.name


def test_sys_modules_guard_keeps_the_chain_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(sys.modules):
        if name.split(".")[0] in _BLOCKED:
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def _guard(name: str, *args: object, **kwargs: object):
        if name.split(".")[0] in _BLOCKED:
            raise ModuleNotFoundError(f"blocked for the test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guard)
    for module in (
        "corpus_kb.tools.nli_tools",
        "corpus_kb.tools.vector_screen_tools",
        "corpus_kb.tools.neighbor_tools",
        "corpus_kb.tools.centroid_tools",
        "corpus_kb.tools.cluster_tools",
        "corpus_kb.analysis.vectors",
        "corpus_kb.analysis.nli_contract",
    ):
        importlib.import_module(module)

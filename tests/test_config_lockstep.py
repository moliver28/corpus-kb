"""Tests for the packaged-config lockstep gate (issue #246).

The gate (corpus_kb._setup.config_lockstep) fails when the code-side
defaults and the PACKAGED config.yaml disagree on any leaf. The real-repo
test below is the hard CI enforcement for the PyYAML-only validate job,
which reports a clean skip when it cannot import corpus_kb.
"""

from __future__ import annotations

import sys

import pytest

from corpus_kb._setup.config_lockstep import (
    check_packaged_lockstep,
    flatten_leaves,
    leaf_diffs,
)


class TestFlattenLeaves:
    def test_nested_dicts_become_dotted_paths(self) -> None:
        tree: dict[str, object] = {"a": {"b": {"c": 1}}}
        assert flatten_leaves(tree) == {"a.b.c": 1}

    def test_lists_are_single_leaves(self) -> None:
        tree: dict[str, object] = {"a": {"list": [1, 2], "name": "x"}}
        assert flatten_leaves(tree) == {"a.list": [1, 2], "a.name": "x"}

    def test_none_and_bools_are_leaves(self) -> None:
        tree: dict[str, object] = {"a": None, "b": {"flag": False}}
        assert flatten_leaves(tree) == {"a": None, "b.flag": False}

    def test_empty_dict_yields_no_leaves(self) -> None:
        assert flatten_leaves({}) == {}


class TestLeafDiffs:
    def test_equal_trees_yield_no_diffs(self) -> None:
        tree: dict[str, object] = {"a": {"b": 1, "c": [1, 2]}}
        assert leaf_diffs(tree, tree) == []

    def test_value_diff_names_path_and_both_values(self) -> None:
        code: dict[str, object] = {"extraction": {"backend": "pgml"}}
        packaged: dict[str, object] = {"extraction": {"backend": "ml"}}
        assert leaf_diffs(code, packaged) == ["extraction.backend: code='pgml' packaged='ml'"]

    def test_code_only_leaf_reports_missing_from_packaged(self) -> None:
        code: dict[str, object] = {"analysis": {"labeling": {"enabled": False}}}
        packaged: dict[str, object] = {}
        assert leaf_diffs(code, packaged) == [
            "analysis.labeling.enabled: missing from packaged config.yaml (code=False)"
        ]

    def test_yaml_only_leaf_reports_missing_from_code(self) -> None:
        code: dict[str, object] = {}
        packaged: dict[str, object] = {"web": {"max_pages": 5}}
        assert leaf_diffs(code, packaged) == [
            "web.max_pages: missing from get_default_config() (packaged=5)"
        ]

    def test_list_value_whole_comparison(self) -> None:
        code: dict[str, object] = {"halt_on": ["a", "b"]}
        packaged: dict[str, object] = {"halt_on": ["a", "b", "c"]}
        assert leaf_diffs(code, packaged) == ["halt_on: code=['a', 'b'] packaged=['a', 'b', 'c']"]

    def test_output_order_is_deterministic(self) -> None:
        code: dict[str, object] = {"z": 1, "a": {"m": 2, "k": 3}}
        packaged: dict[str, object] = {"z": 9, "a": {"m": 8}}
        diffs = leaf_diffs(code, packaged)
        paths = [d.split(":")[0] for d in diffs]
        # Value diffs first (sorted), then code-only, then yaml-only.
        assert paths == ["a.m", "z", "a.k"]


class TestCheckPackagedLockstep:
    def test_real_repo_defaults_equal_packaged_yaml(self) -> None:
        """THE enforcement: zero drift between code defaults and packaged yaml.

        Runs in the full-install test job on every CI leg; the slim
        validate-configs job skips the same check (no corpus_kb import).
        """
        result = check_packaged_lockstep()
        assert result.skipped is False
        assert result.errors == [], "\n".join(result.errors)
        assert "OK" in result.summary

    def test_fabricated_drift_fails_with_named_leaf(self) -> None:
        result = check_packaged_lockstep(
            code_config={"extraction": {"backend": "pgml"}},
            packaged={"extraction": {"backend": "ml"}},
        )
        assert result.skipped is False
        assert result.errors == ["extraction.backend: code='pgml' packaged='ml'"]
        assert "FAILED" in result.summary

    def test_skip_when_package_not_importable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The PyYAML-only CI job has no corpus_kb; the check must skip, not raise."""
        monkeypatch.setitem(sys.modules, "corpus_kb.config", None)
        result = check_packaged_lockstep()
        assert result.skipped is True
        assert result.errors == []
        assert "skipped" in result.summary

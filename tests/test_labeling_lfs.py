"""LF file loading (schema validation) and the four built-in scorers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from corpus_kb.api._body import error_response
from corpus_kb.coding.aggregation import MISSING
from corpus_kb.errors import ValidationError
from corpus_kb.labeling.lfs import load_lfs
from corpus_kb.labeling.protocol import LabelingFunction

# ---------------------------------------------------------------------------
# Loader: happy path + every rejection with its remediation
# ---------------------------------------------------------------------------

_VALID = {
    "classes": ["relevant", "noise"],
    "lfs": [
        {"name": "sla", "kind": "regex", "label": "relevant", "config": {"pattern": r"\bSLA\b"}},
        {
            "name": "uptime",
            "kind": "lexicon",
            "label": "relevant",
            "config": {"terms": ["uptime", "downtime"]},
        },
        {
            "name": "neg-uptime",
            "kind": "negation_regex",
            "label": "noise",
            "config": {"pattern": r"\buptime\b", "negation_window": 12},
        },
        {
            "name": "web-faq",
            "kind": "metadata",
            "label": "noise",
            "config": {"key": "og:type", "equals": "faq"},
        },
    ],
}


def _write(tmp_path: Any, doc: object) -> str:
    path = tmp_path / "lfs.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_load_assigns_indices_in_class_order(tmp_path: Any) -> None:
    spec = load_lfs(_write(tmp_path, _VALID))
    assert spec.classes == ("relevant", "noise")
    assert [lf.name for lf in spec.lfs] == ["sla", "uptime", "neg-uptime", "web-faq"]
    assert [lf.label_index for lf in spec.lfs] == [0, 0, 1, 1]


def test_load_accepts_zero_lfs(tmp_path: Any) -> None:
    spec = load_lfs(_write(tmp_path, {"classes": ["a"], "lfs": []}))
    assert spec.classes == ("a",)
    assert spec.lfs == ()


def test_invalid_json_names_the_line(tmp_path: Any) -> None:
    path = tmp_path / "lfs.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError, match="not valid JSON"):
        load_lfs(str(path))


@pytest.mark.parametrize(
    "doc",
    [
        {"classes": [], "lfs": []},
        {"lfs": []},
        {"classes": ["a", "a"], "lfs": []},
        {"classes": ["a"], "lfs": {}},
        ["not", "an", "object"],
    ],
)
def test_bad_top_level_shapes_rejected(tmp_path: Any, doc: object) -> None:
    with pytest.raises(ValidationError):
        load_lfs(_write(tmp_path, doc))


def test_class_names_must_be_non_empty_strings(tmp_path: Any) -> None:
    doc = {"classes": [""], "lfs": []}
    with pytest.raises(ValidationError, match="every class name must be a non-empty string"):
        load_lfs(_write(tmp_path, doc))


def test_lf_entries_must_be_json_objects(tmp_path: Any) -> None:
    doc = {"classes": ["a"], "lfs": ["x"]}
    with pytest.raises(ValidationError, match='every entry in "lfs" must be a JSON object'):
        load_lfs(_write(tmp_path, doc))


def test_lf_needs_a_non_empty_name(tmp_path: Any) -> None:
    doc = {"classes": ["a"], "lfs": [{"name": "", "kind": "regex", "label": "a", "config": {}}]}
    with pytest.raises(ValidationError, match='non-empty "name"'):
        load_lfs(_write(tmp_path, doc))


def test_lf_config_must_be_a_json_object(tmp_path: Any) -> None:
    doc = {"classes": ["a"], "lfs": [{"name": "x", "kind": "regex", "label": "a", "config": []}]}
    with pytest.raises(ValidationError, match="config must be a JSON object"):
        load_lfs(_write(tmp_path, doc))


def test_terms_must_be_non_empty_strings(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "lexicon", "label": "a", "config": {"terms": [""]}}],
    }
    with pytest.raises(ValidationError, match="every term must be a non-empty string"):
        load_lfs(_write(tmp_path, doc))


def test_case_sensitive_must_be_bool(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [
            {
                "name": "x",
                "kind": "lexicon",
                "label": "a",
                "config": {"terms": ["a"], "case_sensitive": "yes"},
            }
        ],
    }
    with pytest.raises(ValidationError, match="case_sensitive must be true or false"):
        load_lfs(_write(tmp_path, doc))


def test_required_config_strings_cannot_be_empty(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [
            {"name": "x", "kind": "metadata", "label": "a", "config": {"key": "", "equals": "x"}}
        ],
    }
    with pytest.raises(ValidationError, match=r"config\.key must be a non-empty string"):
        load_lfs(_write(tmp_path, doc))


def test_unknown_kind_names_the_supported_set(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "spells", "label": "a", "config": {}}],
    }
    supported = "Supported kinds: lexicon, metadata, negation_regex, regex"
    with pytest.raises(ValidationError, match=supported):
        load_lfs(_write(tmp_path, doc))


def test_unknown_class_names_the_vocabulary(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "regex", "label": "b", "config": {"pattern": "x"}}],
    }
    with pytest.raises(ValidationError, match="unknown class 'b'"):
        load_lfs(_write(tmp_path, doc))


def test_duplicate_lf_names_rejected(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [
            {"name": "x", "kind": "regex", "label": "a", "config": {"pattern": "x"}},
            {"name": "x", "kind": "regex", "label": "a", "config": {"pattern": "y"}},
        ],
    }
    with pytest.raises(ValidationError, match='duplicate LF name "x"'):
        load_lfs(_write(tmp_path, doc))


def test_invalid_regex_fails_at_load(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "regex", "label": "a", "config": {"pattern": "(("}}],
    }
    with pytest.raises(ValidationError):
        load_lfs(_write(tmp_path, doc))


def test_unknown_config_keys_rejected_as_typos(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [
            {"name": "x", "kind": "regex", "label": "a", "config": {"patten": "x"}},
        ],
    }
    with pytest.raises(ValidationError, match="unknown config keys"):
        load_lfs(_write(tmp_path, doc))


def test_negation_window_must_be_positive(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [
            {
                "name": "x",
                "kind": "negation_regex",
                "label": "a",
                "config": {"pattern": "x", "negation_window": 0},
            },
        ],
    }
    with pytest.raises(ValidationError, match="negation_window"):
        load_lfs(_write(tmp_path, doc))


def test_lexicon_needs_terms(tmp_path: Any) -> None:
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "lexicon", "label": "a", "config": {"terms": []}}],
    }
    with pytest.raises(ValidationError, match="terms"):
        load_lfs(_write(tmp_path, doc))


def test_rejected_lf_file_keeps_the_400_wire_contract(tmp_path: Any) -> None:
    """A rejected LF file answers 400 and CLI exit 1 (issue 229, wave 1).

    The wave-1 retype holds the wire triple at (400, -32602, 1).
    """
    doc = {
        "classes": ["a"],
        "lfs": [{"name": "x", "kind": "regex", "label": "a", "config": {"pattern": "(("}}],
    }
    with pytest.raises(ValidationError) as excinfo:
        load_lfs(_write(tmp_path, doc))
    exc = excinfo.value
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == (400, -32602, 1)
    assert error_response(exc).status_code == 400


# ---------------------------------------------------------------------------
# Built-in scorers through LabelingFunction.apply (index or MISSING)
# ---------------------------------------------------------------------------


def _lf(kind: str, config: dict[str, object], label_index: int = 0) -> LabelingFunction:
    return LabelingFunction(name="t", kind=kind, label="a", label_index=label_index, config=config)


def test_regex_fires_and_abstains() -> None:
    lf = _lf("regex", {"pattern": r"\bSLA\b"})
    assert lf.apply("The SLA is clear.", {}) == 0
    assert lf.apply("The sla is lowercase.", {}) == MISSING  # case-sensitive
    assert lf.apply("Usage of the service.", {}) == MISSING


def test_lexicon_word_boundaries_and_case() -> None:
    lf = _lf("lexicon", {"terms": ["no", "go"]})
    assert lf.apply("Say NO loudly.", {}) == 0  # case-insensitive default
    assert lf.apply("Nothing goes north.", {}) == MISSING  # no word-boundary match
    strict = _lf("lexicon", {"terms": ["no"], "case_sensitive": True})
    assert strict.apply("Say NO.", {}) == MISSING
    assert strict.apply("Say no.", {}) == 0


def test_negation_regex_respects_the_window() -> None:
    lf = _lf("negation_regex", {"pattern": r"\buptime\b", "negation_window": 15})
    assert lf.apply("We promise uptime always.", {}) == 0
    # "no" sits within 15 chars before the match -> the match is negated.
    assert lf.apply("But there is no uptime at all.", {}) == MISSING
    # The negator is farther back than the window -> the match stands.
    assert lf.apply("No one said we lack uptime here.", {}) == 0
    # Contraction negators count too ("sn't" inside the window).
    assert lf.apply("It doesn't promise uptime.", {}) == MISSING


def test_negation_regex_all_matches_negated_abstains() -> None:
    lf = _lf("negation_regex", {"pattern": r"\bug\b", "negation_window": 20})
    assert lf.apply("no ug and never ug again", {}) == MISSING
    assert lf.apply("no ug but ug", {}) == MISSING  # only negated occurrences


def test_metadata_predicate_reads_exact_string_values() -> None:
    lf = _lf("metadata", {"key": "og:type", "equals": "faq"})
    assert lf.apply("text", {"og:type": "faq"}) == 0
    assert lf.apply("text", {"og:type": "blog"}) == MISSING
    assert lf.apply("text", {}) == MISSING
    assert lf.apply("text", {"og:type": 3}) == MISSING  # non-string never equals

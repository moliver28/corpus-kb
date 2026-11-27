"""Prompt registry (TD-03): loader validation, strict variables, render digest.

The packaged prompts (coder_cell, theory, resample, group_boundary) are the
real fixtures; malformed documents are crafted in-process through the pure
``_spec_from_doc`` so no file writes touch the package.
"""

from __future__ import annotations

import pytest

from corpus_kb.coding.prompt_registry import (
    PromptNotFoundError,
    PromptRenderError,
    _spec_from_doc,
    load_prompt,
    render_prompt,
)


def test_known_prompts_load_with_headers() -> None:
    for prompt_id, kind, n_vars in (
        ("coder_cell", "cell", 3),
        ("theory", "cell", 3),
        ("resample", "cell", 3),
        ("group_boundary", "group_boundary", 2),
    ):
        spec = load_prompt(prompt_id)
        assert spec.id == prompt_id
        assert spec.version == "1.0.0"
        assert spec.kind == kind
        assert len(spec.variables) == n_vars
        assert spec.body  # the body is non-empty text


def test_unknown_id_raises_not_found() -> None:
    with pytest.raises(PromptNotFoundError) as exc:
        load_prompt("no_such_prompt")
    assert exc.value.error_type == "not_found"


def test_bad_id_shape_raises_not_found() -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("../escape")


def test_missing_required_variable_is_typed() -> None:
    with pytest.raises(PromptRenderError) as exc:
        render_prompt("theory", {"definition": "d"})
    assert "missing required variables" in str(exc.value)
    assert exc.value.error_type == "validation_error"
    assert exc.value.http_status == 400


def test_unknown_variable_is_typed() -> None:
    with pytest.raises(PromptRenderError) as exc:
        render_prompt("resample", {"code": "c", "definition": "d", "text": "t", "zz": "1"})
    assert "unknown variables" in str(exc.value)


def test_optional_variable_fills_from_default() -> None:
    rendered = render_prompt("coder_cell", {"candidate_codes": "codes", "target_chunk": "text"})
    assert rendered.rendered.startswith("You are coding one transcript turn")
    assert rendered.prompt_id == "coder_cell"
    assert rendered.prompt_version == "1.0.0"


def test_render_digest_stable_and_value_sensitive() -> None:
    a = render_prompt("theory", {"definition": "d", "inclusion": "i", "exclusion": "e"})
    b = render_prompt("theory", {"definition": "d", "inclusion": "i", "exclusion": "e"})
    c = render_prompt("theory", {"definition": "OTHER", "inclusion": "i", "exclusion": "e"})
    assert a.prompt_digest == b.prompt_digest
    assert a.prompt_digest != c.prompt_digest
    assert len(a.prompt_digest) == 64  # sha256 hex


def test_digest_covers_id_and_version() -> None:
    theory = render_prompt("theory", {"definition": "d", "inclusion": "i", "exclusion": "e"})
    cell = render_prompt("coder_cell", {"candidate_codes": "x", "target_chunk": "y"})
    assert theory.prompt_digest != cell.prompt_digest


def test_render_substitutes_exactly() -> None:
    rendered = render_prompt("resample", {"code": "C1", "definition": "def", "text": "unit"})
    assert 'Code: "C1" - def' in rendered.rendered
    assert "{{" not in rendered.rendered


def test_group_boundary_is_arbiter_kind() -> None:
    spec = load_prompt("group_boundary")
    assert spec.kind == "group_boundary"
    assert "never accepts or rejects a code on its own" in spec.body


def _base_doc() -> dict[str, object]:
    return {
        "id": "x",
        "version": "1.0.0",
        "kind": "cell",
        "changelog": "",
        "variables": [{"name": "a", "description": "", "required": True}],
        "defaults": {"temperature": 0},
        "body": "Hello {{a}}",
    }


def test_malformed_header_keys_fail_loud() -> None:
    doc = _base_doc()
    doc["zzz"] = 1
    with pytest.raises(PromptRenderError, match="unknown header keys"):
        _spec_from_doc("x", doc)


def test_misdeclared_variables_fail_at_load() -> None:
    doc = _base_doc()
    doc["body"] = "Hello {{b}}"  # placeholder not declared
    with pytest.raises(PromptRenderError, match="undeclared variables"):
        _spec_from_doc("x", doc)
    doc2 = _base_doc()
    doc2["variables"] = [
        {"name": "a", "description": "", "required": True},
        {"name": "dead", "description": "", "required": False},
    ]
    with pytest.raises(PromptRenderError, match="missing from body"):
        _spec_from_doc("x", doc2)


def test_id_mismatch_fails_loud() -> None:
    with pytest.raises(PromptRenderError, match="does not match"):
        _spec_from_doc("y", _base_doc())

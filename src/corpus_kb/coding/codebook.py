"""Codebook validation, content hashing, and theory grounding.

The loader that consumes all three lives in codebook_loader.py: it is three
transaction phases wrapped around the embedding calls, and keeping it here put
this file well past the 250-line soft limit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, TypedDict

from corpus_kb.coding.prompt_registry import render_prompt


class CodebookError(ValueError):
    """Raised when codebook validation fails."""


class TheoryFields(TypedDict, total=False):
    """Academic grounding fields for a code."""

    tradition: str | None
    construct: str | None
    citation: str | None
    note: str | None


MACQUEEN_FIELDS = {
    "name",
    "brief_definition",
    "inclusion_criteria",
    "exclusion_criteria",
    "examples",
    "theory",
}

VALID_PARADIGMS = {"codebook", "reflexive"}

KEYWORD_KEYS = {"include", "exclude"}

# TAO T1 (TD-03): the theory prompt moved into coding/prompts/theory.yaml;
# propose_theory renders it through the prompt registry (one renderer, one
# header convention). THEOREY_PROMPT_ID pins the registry id.
THEORY_PROMPT_ID = "theory"


def _validate_keywords(code: dict[str, Any]) -> None:
    """Validate one code's optional ``keywords`` block (TD-10).

    ``include`` and ``exclude`` are lists of non-empty strings; no other key
    is allowed. One rule: an exclusion list needs an inclusion list or
    exclusion_criteria text — an exclusion needs something to block.
    """
    label = code.get("code_id") or code.get("id") or "?"
    keywords = code.get("keywords")
    if keywords is None:
        return
    if not isinstance(keywords, dict):
        raise CodebookError(f"Code {label} keywords must be an object")
    unknown = set(keywords) - KEYWORD_KEYS
    if unknown:
        raise CodebookError(f"Code {label} keywords has unknown keys: {sorted(unknown)}")
    for key in ("include", "exclude"):
        terms = keywords.get(key)
        if terms is None:
            continue
        if not isinstance(terms, list) or not all(
            isinstance(term, str) and term.strip() for term in terms
        ):
            raise CodebookError(f"Code {label} keywords.{key} must be a list of strings")
    has_include = bool(keywords.get("include"))
    has_exclusion_text = bool(str(code.get("exclusion_criteria") or "").strip())
    if keywords.get("exclude") and not has_include and not has_exclusion_text:
        raise CodebookError(
            f"Code {label} has exclusion keywords but no include list and no "
            "exclusion_criteria text. An exclusion needs something to block."
        )


def validate_codebook(doc: dict[str, Any]) -> None:
    """
    Validate a codebook document structure.

    Asserts:
    - Top-level paradigm is in {codebook, reflexive}
    - Each code in categories has all MacQueen fields
    - theory field exists (may be null or {})

    Args:
        doc: The codebook document (typically parsed JSON)

    Raises:
        CodebookError: If validation fails
    """
    if not isinstance(doc, dict):
        raise CodebookError("Codebook must be a dictionary")

    # Check paradigm
    paradigm = doc.get("paradigm")
    if paradigm not in VALID_PARADIGMS:
        raise CodebookError(f"Top-level paradigm must be in {VALID_PARADIGMS}, got: {paradigm!r}")

    # Check categories
    categories = doc.get("categories", [])
    if not isinstance(categories, list):
        raise CodebookError("categories must be a list")

    for i, code in enumerate(categories):
        if not isinstance(code, dict):
            raise CodebookError(f"Code at index {i} is not a dict")

        missing = MACQUEEN_FIELDS - set(code.keys())
        if missing:
            raise CodebookError(f"Code {code.get('id', i)} missing MacQueen fields: {missing}")

        # Validate theory field exists (may be null or {})
        if "theory" not in code:
            raise CodebookError(f"Code {code.get('id', i)} missing theory field")

        _validate_keywords(code)


def codebook_sha256(doc: dict[str, Any]) -> str:
    """
    Compute SHA256 hash of codebook for versioning.

    Uses a deterministic JSON serialization (sorted keys, no whitespace).

    Args:
        doc: The codebook document

    Returns:
        Hex SHA256 digest
    """
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def propose_theory(
    code: dict[str, Any],
    call_fn: Callable[[str], Any],
) -> TheoryFields:
    """
    Propose academic grounding for a code by calling a language model.

    The model is instructed to return null for any field it cannot ground in
    the code's own definition and inclusion/exclusion criteria. This prevents
    confabulation of citations.

    Args:
        code: The code dict with at least name, brief_definition,
              inclusion_criteria, exclusion_criteria
        call_fn: Async function that takes a prompt string and returns
                 a parsed response (dict or calls model directly)

    Returns:
        A dict with keys {tradition, construct, citation, note}, where
        any field the model could not ground is null. Always returns a
        dict (never confabulates).
    """
    definition = code.get("brief_definition", "")
    inclusion = code.get("inclusion_criteria", "")
    exclusion = code.get("exclusion_criteria", "")

    # The registry render replaces the old .replace chain (which existed
    # because .format would interpolate the prompt's literal JSON braces).
    prompt = render_prompt(
        THEORY_PROMPT_ID,
        {"definition": definition, "inclusion": inclusion, "exclusion": exclusion},
    ).rendered.rstrip("\n")

    response = await call_fn(prompt)

    # Parse response; ensure all fields present and null where ungrounded
    result: TheoryFields = {
        "tradition": None,
        "construct": None,
        "citation": None,
        "note": None,
    }

    if isinstance(response, dict):
        for field in result:
            if field in response:
                result[field] = response.get(field)

    return result

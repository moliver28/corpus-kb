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

THEORY_PROMPT = """You are a qualitative researcher. Given a code's definition
and criteria, propose academic grounding for it.

Return a JSON object with these fields. Return null (not "null" string, actual JSON null) for any
field you cannot ground in the code's definition alone. Never invent a citation; never cite what
the code's definition does not explicitly state.

Fields:
- tradition: the research tradition or theoretical framework
  (e.g. "Grounded Theory", "Phenomenology")
- construct: the core construct being measured or observed
- citation: a full citation (author(s) year) if applicable, null otherwise
- note: a brief explanatory note if helpful, null otherwise

Worked null example:
Input code definition: "the participant paused for more than 2 seconds"
Output: {"tradition": null, "construct": null, "citation": null, "note": null}

Code definition: {definition}
Inclusion criteria: {inclusion}
Exclusion criteria: {exclusion}

Return only valid JSON, no markdown, no preamble."""


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

    # .format would interpolate the prompt's literal JSON braces (KeyError '"tradition"');
    # only these three slots are substitution targets.
    prompt = (
        THEORY_PROMPT.replace("{definition}", definition)
        .replace("{inclusion}", inclusion)
        .replace("{exclusion}", exclusion)
    )

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

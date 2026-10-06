from __future__ import annotations

import pytest

from corpus_kb.coding.codebook import CodebookError, codebook_sha256, validate_codebook


def test_validate_requires_macqueen_fields_and_paradigm() -> None:
    good = {
        "paradigm": "codebook",
        "categories": [
            {
                "id": "C1",
                "name": "n",
                "brief_definition": "b",
                "inclusion_criteria": "i",
                "exclusion_criteria": "e",
                "examples": [],
                "theory": {},
            }
        ],
    }
    validate_codebook(good)  # no raise
    assert isinstance(codebook_sha256(good), str)
    with pytest.raises(CodebookError):
        validate_codebook({"categories": [{"id": "C1", "name": "n"}]})  # missing fields + paradigm

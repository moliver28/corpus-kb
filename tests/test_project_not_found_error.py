"""W4-3 pins the two typed claim/master refusal rows in the taxonomy.

``project_not_found`` answers the not-found triple; ``source_type_unknown``
takes the next free server-defined socket slot (-32006) with the bad-input
status and exit. Both classes extend the P1 taxonomy table in place —
never a parallel error path.
"""

from __future__ import annotations

from corpus_kb.contracts.model import error_triplet
from corpus_kb.errors import ERROR_MAP, ProjectNotFoundError, SourceTypeUnknownError


def test_project_not_found_row() -> None:
    assert ProjectNotFoundError("x").error_type == "project_not_found"
    assert error_triplet("project_not_found") == (404, -32001, 1)
    assert ProjectNotFoundError("x").http_status == 404


def test_source_type_unknown_row() -> None:
    assert SourceTypeUnknownError("x").error_type == "source_type_unknown"
    assert error_triplet("source_type_unknown") == (400, -32006, 1)


def test_source_type_socket_code_is_new() -> None:
    users = [name for name, triplet in ERROR_MAP.items() if triplet[1] == -32006]
    assert users == ["source_type_unknown"]


def test_error_map_keys_stay_unique() -> None:
    assert len(ERROR_MAP) == len(set(ERROR_MAP))

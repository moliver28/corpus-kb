"""Ontology loader tolerance: optional version + per-type descriptions."""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.ontology import load_ontology

_PACKAGED_ONTOLOGY = (
    Path(__file__).resolve().parent.parent / "src" / "corpus_kb" / "config" / "ontology.yaml"
)


def test_packaged_ontology_without_optional_fields_loads() -> None:
    """Absence of version/descriptions is tolerated (existing file unchanged)."""
    ontology = load_ontology(_PACKAGED_ONTOLOGY)
    assert ontology.version is None
    assert ontology.entity_descriptions == {}
    assert ontology.relation_descriptions == {}
    assert "Person" in ontology.entity_types


def test_loads_version_and_descriptions_from_mapping_entries(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "version: '2'\n"
        "entity_types:\n"
        "  - name: Person\n"
        "    description: A human individual\n"
        "  - Org\n"
        "relation_types:\n"
        "  - RELATED_TO\n",
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    assert ontology.version == "2"
    assert ontology.entity_types == ["Person", "Org"]
    assert ontology.entity_descriptions == {"Person": "A human individual"}
    assert ontology.relation_descriptions == {}


def test_plain_string_ontology_still_loads_identically(tmp_path: Path) -> None:
    """The pre-existing flat-string shape is unchanged by the new fields."""
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\n  - Org\nrelation_types:\n  - RELATED_TO\n",
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    assert ontology.entity_types == ["Person", "Org"]
    assert ontology.version is None
    assert ontology.entity_descriptions == {}


def test_top_level_descriptions_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "version: '3'\n"
        "entity_types:\n  - Person\nrelation_types:\n  - RELATED_TO\n"
        "entity_descriptions:\n  Person: Human being\n",
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    assert ontology.entity_descriptions == {"Person": "Human being"}


def test_description_for_undeclared_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\nrelation_types:\n  - RELATED_TO\n"
        "entity_descriptions:\n  Whovian: Not declared\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not declared"):
        load_ontology(path)


def test_inline_description_for_undeclared_entry_is_preserved_and_rejected(tmp_path: Path) -> None:
    """A mapping entry whose description references nothing is still validated."""
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n"
        "  - Person\n"
        "  - name: Ghost\n"
        "    description: Spooky\n"
        "relation_types:\n  - RELATED_TO\n",
        encoding="utf-8",
    )
    ontology = load_ontology(path)
    assert ontology.entity_descriptions == {"Ghost": "Spooky"}


def test_duplicate_type_names_across_shapes_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text(
        "entity_types:\n  - Person\n  - name: Person\nrelation_types:\n  - RELATED_TO\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique"):
        load_ontology(path)


def test_non_string_non_mapping_entry_is_rejected_loudly(tmp_path: Path) -> None:
    path = tmp_path / "ontology.yaml"
    path.write_text("entity_types:\n  - 42\nrelation_types:\n  - RELATED_TO\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed ontology file"):
        load_ontology(path)

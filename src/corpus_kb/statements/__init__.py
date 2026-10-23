"""Statement atoms: the proposition layer (Living Corpus W2).

``statements.templates`` — the validated claim-template catalogue.
``statements.atomize`` — deterministic chunk-to-atoms atomization.
``statements.store`` — write-once persistence plus the two read paths.
"""

from __future__ import annotations

from .atomize import StatementAtom, atomize_chunk, statement_row, template_histogram
from .store import StatementStore
from .templates import TemplateCatalogue, TemplateSpec, load_templates

__all__ = [
    "StatementAtom",
    "StatementStore",
    "TemplateCatalogue",
    "TemplateSpec",
    "atomize_chunk",
    "load_templates",
    "statement_row",
    "template_histogram",
]

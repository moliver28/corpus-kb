"""XXE hardening: untrusted XML parsers reject entities (issue #209).

Both sinks parse attacker-controllable bytes: REFI-QDA project imports and
pptx part XML (app.xml Company). The parsers are defusedxml, so a DTD with
entities must raise instead of expanding — no file reads, no network reads.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from corpus_kb.coding.refi_qda import REFI_QDA_NAMESPACE, RefiQdaError, import_project

_BOMB_DOCTYPE = (
    "<!DOCTYPE Project ["
    "<!ENTITY a 'aaaaaaaaaa'>"
    "<!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;'>"
    "<!ENTITY c '&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;'>"
    "<!ENTITY d '&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;'>"
    "]>"
)


def _project_xml(doctype: str) -> str:
    return (
        '<?xml version="1.0"?>' + doctype + f'<Project xmlns="{REFI_QDA_NAMESPACE}">'
        "<CodeBook><Codes/></CodeBook>"
        "</Project>"
    )


def test_import_rejects_entity_expansion() -> None:
    """A billion-laughs style DTD is refused, not expanded."""
    with pytest.raises(RefiQdaError, match="rejected"):
        import_project(_project_xml(_BOMB_DOCTYPE))


def test_import_rejects_external_entity() -> None:
    """An external-entity DTD is refused before any file is read."""
    doctype = '<!DOCTYPE Project [<!ENTITY xxe SYSTEM "file:///c:/windows/win.ini">]>'
    with pytest.raises(RefiQdaError, match="rejected"):
        import_project(_project_xml(doctype))


@dataclass(frozen=True)
class _FakePart:
    partname: str
    blob: bytes


@dataclass(frozen=True)
class _FakePackage:
    parts: tuple[_FakePart, ...]

    def iter_parts(self) -> Iterator[_FakePart]:
        return iter(self.parts)


@dataclass(frozen=True)
class _FakeDeck:
    part: _FakePackage


def test_pptx_company_rejects_malicious_part_xml() -> None:
    """A malicious app.xml degrades to None (no entity resolution)."""
    from corpus_kb.ingestion.strategies._pptx_parts import _company

    evil = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE Properties [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/'
        b'2006/extended-properties"><Company>&xxe;</Company></Properties>'
    )
    deck = _FakeDeck(_FakePackage((_FakePart("/docProps/app.xml", evil),)))
    assert _company(deck) is None

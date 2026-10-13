"""Reusable .pptx deck factory for ingestion tests (built in-test, no binaries).

:func:`make_research_deck` writes a deterministic ~10-slide research deck
("Grounded RAG Survey: Corpus Evaluation Methodology") with a comparison
table, multi-paragraph bodies, speaker notes on six slides, and populated
core + extended (company) properties. Content is inline so assertions can be
exact; no binary fixture is committed. The e2e suite reuses the same factory.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.slide import Slide

DECK_TITLE = "Grounded RAG Survey: Corpus Evaluation Methodology"
DECK_AUTHOR = "Dr. Ada Lovelace"
DECK_COMPANY = "Corpus Research Lab"
DECK_SUBJECT = "Corpus evaluation methodology"
DECK_CREATED = datetime.datetime(2026, 1, 15, 10, 30, 0)
DECK_MODIFIED = datetime.datetime(2026, 2, 20, 14, 0, 0)

TABLE_TITLE = "Hybrid Retrieval Comparison"
TABLE_HEADER = ("Approach", "Recall@10", "P95 latency (ms)")
TABLE_ROWS = (
    ("Dense only (nomic)", "0.71", "84"),
    ("Lexical BM25 only", "0.58", "12"),
    ("Hybrid RRF fusion", "0.91", "96"),
)

# Slides carrying speaker notes (1-based) — the notes-chunk count assertion.
NOTED_SLIDES = {1, 2, 3, 5, 7, 10}
SLIDE_COUNT = 10

_EXTENDED_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"


def make_research_deck(path: Path) -> Path:
    """Write the research survey deck (10 slides, 6 noted) to ``path``."""
    deck = Presentation()
    props = deck.core_properties
    props.author = DECK_AUTHOR
    props.title = DECK_TITLE
    props.subject = DECK_SUBJECT
    props.created = DECK_CREATED
    props.modified = DECK_MODIFIED

    _title_slide(deck)
    _bullets_slide(
        deck,
        "Agenda",
        ["1. Corpus construction", "2. Metric taxonomy", "3. Hybrid retrieval", "4. Verdicts"],
        notes="Preview the four sections before diving in.",
    )
    _bullets_slide(
        deck,
        "Corpus Construction",
        [
            "Corpus: 12k engineering documents across five repositories.",
            "Chunking: heading-aware for markdown, AST for code, slides for decks.",
            "Gold labels: 340 query-passage pairs rated by two annotators.",
        ],
        notes="Emphasize that the corpus spans more than markdown.",
    )
    _table_slide(deck)
    _paragraph_slide(
        deck,
        "Metric Taxonomy",
        [
            "Recall@k anchors retrieval quality at fixed depth.",
            "nDCG rewards ranking the gold passage above near-misses.",
            "Answer faithfulness is graded against the retrieved span only.",
            "Latency is reported at P50 and P95 under a 32-way load.",
            "Every metric is computed per source type, then aggregated.",
        ],
        notes="Faithfulness grading is the most expensive metric to run.",
    )
    _bullets_slide(
        deck,
        "Grounding and Citations",
        [
            "Every answer must cite the chunk ids it used.",
            "Uncited claims are flagged, not silently dropped.",
            "Citation precision is measured against annotator highlights.",
        ],
    )
    _paragraph_slide(
        deck,
        "Failure Modes",
        [
            "Stale chunks: the corpus snapshot drifts from the repository head.",
            "Table flattening: naive extractors destroy row semantics.",
            "Cross-slide context: a deck argues across slides, not within one.",
            "Speaker notes: surface content and notes disagree on emphasis.",
        ],
        notes="Table flattening is the failure mode this strategy fixes.",
    )
    _bullets_slide(
        deck,
        "Latency Budget",
        [
            "Embedding: 768-dim nomic, batched at 32.",
            "Hybrid fusion: SQL-side RRF, no second round trip.",
            "Reranking: cross-encoder only when the margin is thin.",
        ],
    )
    _paragraph_slide(
        deck,
        "Verdicts",
        [
            "Hybrid RRF wins recall at comparable latency.",
            "Slide-aware ingestion beats flattened text on table queries.",
            "Notes chunks answer emphasis questions body text cannot.",
        ],
    )
    _bullets_slide(
        deck,
        "Questions and Next Steps",
        ["Which corpora should we add next?", "Volunteers for the faithfulness rubric review."],
        notes="Close by assigning the rubric review to two annotators.",
    )

    deck.save(path)
    _set_company(path, DECK_COMPANY)
    return path


def make_blank_deck(path: Path) -> Path:
    """A zero-slide deck (the empty_reason path)."""
    Presentation().save(path)
    return path


def make_oversized_deck(path: Path) -> Path:
    """One slide whose body exceeds the token budget (the split path).

    Five ~1950-char paragraphs plus one ~7000-char paragraph: paragraph
    packing must split the body into budget-sized parts, and the huge final
    paragraph must be hard-split (no chunk may exceed the budget).
    """
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Oversized body"
    body = slide.placeholders[1].text_frame
    paragraphs = [f"gap analysis {i} of the retrieval corpus benchmark. " * 130 for i in range(5)]
    paragraphs.append("overflow paragraph without paragraph breaks. " * 175)
    body.text = paragraphs[0]
    for text in paragraphs[1:]:
        body.add_paragraph().text = text
    deck.save(path)
    return path


def _title_slide(deck: Presentation) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = DECK_TITLE
    slide.placeholders[1].text = "Evaluation methodology for grounded retrieval"
    _set_notes(slide, "Welcome everyone; today we compare retrieval strategies on the benchmark.")


def _bullets_slide(deck: Presentation, title: str, lines: list[str], notes: str = "") -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.text = lines[0]
    for line in lines[1:]:
        body.add_paragraph().text = line
    if notes:
        _set_notes(slide, notes)


def _paragraph_slide(
    deck: Presentation, title: str, paragraphs: list[str], notes: str = ""
) -> None:
    _bullets_slide(deck, title, paragraphs, notes=notes)


def _table_slide(deck: Presentation) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = TABLE_TITLE
    frame = slide.shapes.add_table(
        len(TABLE_ROWS) + 1, len(TABLE_HEADER), 0, 0, 100, 100
    )  # EMU-less ints are fine for a test fixture
    table = frame.table
    for col, header in enumerate(TABLE_HEADER):
        table.cell(0, col).text = header
    for row, values in enumerate(TABLE_ROWS, start=1):
        for col, value in enumerate(values):
            table.cell(row, col).text = value


def _set_notes(slide: Slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def _set_company(path: Path, company: str) -> None:
    """python-pptx has no extended-properties API: rewrite Company in app.xml."""
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    root = ET.fromstring(entries["docProps/app.xml"])
    node = root.find(f"{_EXTENDED_NS}Company")
    if node is None:
        node = ET.SubElement(root, f"{_EXTENDED_NS}Company")
    node.text = company
    entries["docProps/app.xml"] = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)

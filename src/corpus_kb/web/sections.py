"""HTML -> section model: partition, chrome classification, budget packing.

``unstructured.partition.html`` drops ``<nav>``/``<header>`` wholesale and
emits footer text as ordinary narrative elements, so chrome classification
runs HERE: each partitioned element whose text sits inside a footer/header/
nav container is re-categorized (Footer/Header/Nav) and the EXCLUSION rule
applies at section time — chrome shorter than 40 chars in {Header, Footer}
is dropped, substantive chrome is kept with its element_type in metadata.

Sections group elements under their most recent Title/Header; headings
update the breadcrumb like ``chunking.unstructured_chunker._update_heading_path``
(Title + int category_depth trims the path). Oversized sections split at
element (paragraph) boundaries; a single oversized element is hard-split.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from uuid import uuid4

from lxml import html as lxml_html
from unstructured.partition.html import partition_html

from ..partitioning import ElementProxy

CHROME_CATEGORIES = frozenset({"Header", "Footer"})
CHROME_MIN_TEXT_CHARS = 40
SECTION_HEADING_CATEGORIES = frozenset({"Title", "Header"})
JOIN = "\n\n"


def _proxy_from_element(element: object) -> ElementProxy:
    """Wrap one unstructured element (mirrors partitioning._to_proxy)."""
    text = getattr(element, "text", None)
    category = getattr(element, "category", None)
    element_id = getattr(element, "id", None) or getattr(element, "element_id", None)
    metadata: dict[str, object] = {}
    meta_obj = getattr(element, "metadata", None)
    if meta_obj is not None:
        keys = ("coordinates", "detection_class", "page_number", "category_depth", "text_as_html")
        for key in keys:
            value = getattr(meta_obj, key, None)
            if value is not None:
                metadata[key] = value
    return ElementProxy(
        text=(text if isinstance(text, str) else "").strip(),
        element_type=category if isinstance(category, str) else "Unknown",
        element_id=element_id if isinstance(element_id, str) and element_id else str(uuid4()),
        metadata=metadata,
    )


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _chrome_text_sets(raw: str) -> dict[str, frozenset[str]]:
    """Normalized per-element texts inside every <header>/<footer>/<nav>.

    Classification matches a candidate text EXACTLY against this set — the
    old substring match against the joined chrome text re-categorized short
    real headings ("Contact") that merely appeared inside a footer.
    """
    try:
        doc = lxml_html.fromstring(raw)
    except Exception:
        return {}
    out: dict[str, frozenset[str]] = {}
    for tag in ("footer", "header", "nav"):
        texts: set[str] = set()
        for node in doc.xpath(f"//{tag}"):
            for element in node.iter():
                text = _normalized(" ".join(element.itertext()))
                if text:
                    texts.add(text)
        out[tag] = frozenset(texts)
    return out


def _classify_chrome(raw: str, proxies: list[ElementProxy]) -> list[ElementProxy]:
    """Re-categorize elements whose text is exactly a chrome element text."""
    chrome = _chrome_text_sets(raw)
    if not any(chrome.values()):
        return proxies
    out: list[ElementProxy] = []
    for proxy in proxies:
        text = _normalized(proxy.text)
        category = proxy.element_type
        if text:
            if text in chrome["footer"]:
                category = "Footer"
            elif text in chrome["header"]:
                category = "Header"
            elif text in chrome["nav"]:
                category = "Nav"
        if category != proxy.element_type:
            proxy = replace(proxy, element_type=category)
        out.append(proxy)
    return out


def _table_markdown(table_html: str) -> str:
    """GitHub-markdown pipe table from a <table> HTML fragment."""
    try:
        doc = lxml_html.fromstring(table_html)
    except Exception:
        return ""
    rows: list[list[str]] = []
    for tr in doc.xpath(".//tr"):
        cells = [
            _normalized(" ".join(cell.itertext())).replace("|", "\\|")
            for cell in tr.xpath("./th|./td")
        ]
        if any(cell for cell in cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in padded]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def partition_html_proxies(raw: str) -> list[ElementProxy]:
    """Partition raw HTML and wrap the elements (tables as markdown text)."""
    elements: list[object] = list(partition_html(text=raw, include_metadata=True))
    proxies = [_proxy_from_element(element) for element in elements]
    out: list[ElementProxy] = []
    for proxy in _classify_chrome(raw, proxies):
        if proxy.element_type == "Table":
            table_html = proxy.metadata.get("text_as_html")
            markdown = _table_markdown(table_html) if isinstance(table_html, str) else ""
            if markdown:
                proxy = replace(
                    proxy,
                    text=markdown,
                    metadata={**proxy.metadata, "table_html": table_html},
                )
        out.append(proxy)
    return out


@dataclass
class WebSection:
    """One heading-scoped section: breadcrumb + the elements beneath it."""

    heading_path: list[str]
    elements: list[ElementProxy] = field(default_factory=list)


def _updated_path(path: list[str], proxy: ElementProxy) -> list[str]:
    """Breadcrumb update, mirroring unstructured_chunker._update_heading_path."""
    text = proxy.text.strip()
    depth = proxy.metadata.get("category_depth")
    if proxy.element_type == "Title" and isinstance(depth, int):
        return [*path[:depth], text]
    return [*path, text]


def _is_dropped_chrome(proxy: ElementProxy) -> bool:
    """Chrome rule: drop ONLY short (< 40 chars) Header/Footer elements."""
    if proxy.element_type not in CHROME_CATEGORIES:
        return False
    return len(proxy.text.strip()) < CHROME_MIN_TEXT_CHARS


def group_sections(proxies: list[ElementProxy]) -> list[WebSection]:
    """Group kept elements under their most recent Title/Header heading."""
    sections: list[WebSection] = []
    current: WebSection | None = None
    path: list[str] = []
    for proxy in proxies:
        if not proxy.text.strip() or _is_dropped_chrome(proxy):
            continue
        if proxy.element_type in SECTION_HEADING_CATEGORIES:
            path = _updated_path(path, proxy)
            current = WebSection(heading_path=list(path), elements=[proxy])
            sections.append(current)
            continue
        if current is None:
            current = WebSection(heading_path=[], elements=[])
            sections.append(current)
        current.elements.append(proxy)
    return sections


def _hard_split(text: str, budget: int) -> list[str]:
    """Budget-sized slices, preferring whitespace cut points."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + budget, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if cut > start:
                end = cut + 1
        pieces.append(text[start:end])
        start = end
    return pieces or [text]


def pack_elements(elements: list[ElementProxy], budget: int) -> list[list[ElementProxy]]:
    """Pack elements into groups whose '\\n\\n'-joined text fits the budget.

    Splitting happens at element (paragraph) boundaries; a single element
    over budget is hard-split (its proxy replicated per piece) so no chunk
    ever exceeds the token budget (validate_plan invariant).
    """
    groups: list[list[ElementProxy]] = []
    current: list[ElementProxy] = []
    size = 0
    for element in elements:
        text = element.text.strip()
        if len(text) > budget:
            if current:
                groups.append(current)
                current, size = [], 0
            groups.extend(
                [
                    [
                        ElementProxy(
                            text=piece,
                            element_type=element.element_type,
                            element_id=f"{element.element_id}-{index}",
                        )
                    ]
                    for index, piece in enumerate(_hard_split(text, budget))
                ]
            )
            continue
        extra = len(text) + (len(JOIN) if current else 0)
        if size + extra > budget:
            groups.append(current)
            current, size = [], 0
            extra = len(text)
        current.append(element)
        size += extra
    if current:
        groups.append(current)
    return groups

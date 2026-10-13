"""Web page metadata extraction (lxml): meta tags, OpenGraph, JSON-LD.

``extract_metadata`` returns EXACTLY the keys below, all ``str | None`` —
JSON-serializable by construction, so strategies can merge it straight into
chunk metadata. JSON-LD objects typed Article/ScholarlyArticle/TechArticle/
Report contribute headline/datePublished/dateModified/author ONLY when the
plain meta tags are absent (meta wins; JSON-LD is the fallback).
"""

from __future__ import annotations

import json
import logging
from typing import cast

from lxml import html as lxml_html

from .fetch import FetchedPage

logger = logging.getLogger(__name__)

METADATA_KEYS = (
    "title",
    "description",
    "site_name",
    "author",
    "published_time",
    "modified_time",
    "language",
    "canonical",
)

_JSONLD_TYPES = ("Article", "ScholarlyArticle", "TechArticle", "Report")


def _clean(raw: object) -> str | None:
    """Stripped string, or None for empty/missing (never '')."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def _first(doc: lxml_html.HtmlElement, xpath: str) -> str | None:
    """First XPath string result, stripped (XPaths here all select strings)."""
    results = doc.xpath(xpath)
    for item in results:
        value = _clean(item)
        if value is not None:
            return value
    return None


def _meta_content(doc: lxml_html.HtmlElement, name: str, prop: str) -> str | None:
    """meta[@name] then meta[@property] content, first non-empty wins."""
    return _first(doc, f"//meta[@name='{name}']/@content") or _first(
        doc, f"//meta[@property='{prop}']/@content"
    )


def _og_with_twitter_fallback(doc: lxml_html.HtmlElement, og: str, twitter: str) -> str | None:
    """OpenGraph property first; the twitter:* meta only when og is missing."""
    return _first(doc, f"//meta[@property='{og}']/@content") or _first(
        doc, f"//meta[@name='{twitter}']/@content"
    )


def _is_article_type(type_value: object) -> bool:
    types = type_value if isinstance(type_value, list) else [type_value]
    return any(
        isinstance(item, str) and any(name in item for name in _JSONLD_TYPES) for item in types
    )


def _jsonld_author(value: object) -> str | None:
    """JSON-LD author: str, {name}, or a list of either -> joined names."""
    if isinstance(value, list):
        names = [item for item in (_jsonld_author(entry) for entry in value) if item]
        return ", ".join(names) or None
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return _clean(value.get("name"))
    return None


def _iter_jsonld_objects(doc: lxml_html.HtmlElement) -> list[dict[str, object]]:
    """Parsed JSON-LD objects (top level, list items, and @graph members)."""
    objects: list[dict[str, object]] = []
    for raw in doc.xpath("//script[@type='application/ld+json']/text()"):
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            logger.warning("unparsable JSON-LD block skipped")
            continue
        candidates: list[object] = []
        if isinstance(parsed, list):
            candidates.extend(parsed)
        else:
            candidates.append(parsed)
        for candidate in list(candidates):
            if isinstance(candidate, dict) and isinstance(candidate.get("@graph"), list):
                candidates.extend(candidate["@graph"])
        objects.extend(item for item in candidates if isinstance(item, dict))
    return objects


def extract_metadata(fetched: FetchedPage) -> dict[str, str | None]:
    """The 8-key web metadata dict for one fetched page (all values str|None)."""
    meta: dict[str, str | None] = dict.fromkeys(METADATA_KEYS)
    try:
        doc = lxml_html.fromstring(fetched.html)
    except Exception:
        return meta
    if not isinstance(doc, lxml_html.HtmlElement):
        return meta

    meta["title"] = _first(doc, "//title/text()")
    meta["description"] = _meta_content(doc, "description", "og:description")
    meta["site_name"] = _meta_content(doc, "site_name", "og:site_name")
    meta["author"] = _first(doc, "//meta[@name='author']/@content")
    meta["published_time"] = _meta_content(doc, "article:published_time", "article:published_time")
    meta["modified_time"] = _meta_content(doc, "article:modified_time", "article:modified_time")
    meta["language"] = _clean(doc.get("lang"))
    meta["canonical"] = _first(doc, "//link[@rel='canonical']/@href")

    # OpenGraph first, twitter:* only as fallback (og missing).
    meta["title"] = meta["title"] or _og_with_twitter_fallback(doc, "og:title", "twitter:title")
    meta["description"] = meta["description"] or _og_with_twitter_fallback(
        doc, "og:description", "twitter:description"
    )

    for obj in _iter_jsonld_objects(doc):
        if not _is_article_type(obj.get("@type")):
            continue
        meta["title"] = meta["title"] or _clean(obj.get("headline"))
        meta["published_time"] = meta["published_time"] or _clean(obj.get("datePublished"))
        meta["modified_time"] = meta["modified_time"] or _clean(obj.get("dateModified"))
        meta["author"] = meta["author"] or _jsonld_author(obj.get("author"))
        break
    return cast("dict[str, str | None]", meta)

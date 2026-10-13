"""Website (URL) ingestion: fetch, crawl, metadata, web_structure strategy.

Fully offline: every network flow runs on httpx.MockTransport (the transport
is injected all the way through ingest_url), and the ingest_url end-to-end
tests run the real run_pipeline against the stub-store + fake-pool harness
from test_ingest_integrity (the same offline seam test_ingestion_strategies
uses) — no Postgres, no Ollama.
"""

from __future__ import annotations

import json
from typing import Any, cast

import asyncpg
import httpx
import pytest
from test_ingest_integrity import _FakePool, _LockConn

from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS, SourceProfile, validate_plan
from corpus_kb.ingestion.strategies.web import WEB_STRATEGY_NAME, WebStructureStrategy
from corpus_kb.tools import ingest_common
from corpus_kb.web.fetch import (
    FetchedPage,
    WebFetchConfig,
    WebFetchError,
    crawl,
    fetch_page,
    normalize_url,
)
from corpus_kb.web.metadata import METADATA_KEYS, extract_metadata
from corpus_kb.web.sections import (
    CHROME_CATEGORIES,
    CHROME_MIN_TEXT_CHARS,
    pack_elements,
)
from corpus_kb.web.settings import config_from_mapping

TEST_AGENT = WebFetchConfig().user_agent
ROBOTS_TXT = "User-agent: *\nDisallow: /private\n"

RESEARCH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Deep Reinforcement Learning for Corpus Curation | Example Research</title>
  <meta name="description" content="A study of RL-based corpus curation pipelines.">
  <link rel="canonical" href="https://research.test/papers/deep-rl">
  <meta property="og:title" content="Deep RL for Corpus Curation">
  <meta property="og:description" content="OG description of the corpus curation study.">
  <meta property="og:site_name" content="Example Research">
  <meta property="article:published_time" content="2024-03-01T08:00:00Z">
  <meta property="article:modified_time" content="2024-04-10T09:30:00Z">
  <meta name="author" content="Maria Chen">
  <script type="application/ld+json">
    {"@type": "ScholarlyArticle", "headline": "JSONLD Headline Should Lose",
     "datePublished": "1999-01-01", "dateModified": "1999-01-02",
     "author": {"@type": "Person", "name": "Wrong Author"}}
  </script>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/about">About</a></nav>
  <header><p>Example Research journal banner.</p></header>
  <main><article>
    <h1>Deep Reinforcement Learning for Corpus Curation</h1>
    <p>Intro paragraph standing alone before any subsection begins here.</p>
    <h2>Background</h2>
    <p>Corpus curation decides which documents enter the knowledge base.</p>
    <h3>Related Work</h3>
    <p>Prior art covers heuristic filters and static keyword lexicons only.</p>
    <h2>Methodology</h2>
    <p>We frame curation as a sequential decision problem with delayed reward.</p>
    <p>A second methodology paragraph describes the reward shaping schedule.</p>
    <h2>Results</h2>
    <table>
      <tr><th>Pipeline</th><th>F1</th></tr>
      <tr><td>heuristic</td><td>0.61</td></tr>
      <tr><td>rl-curator</td><td>0.84</td></tr>
    </table>
    <h2>Discussion</h2>
    <blockquote>The reward model dominates every ablation we ran this cycle.</blockquote>
    <h2>References</h2>
    <p id="fn1">1. Chen 2023, Curation as Choice Modeling, Example Press.</p>
    <p id="fn2">2. Diaz 2024, Reward Shaping for Librarians, Journal of Bases.</p>
  </article>
  <footer>
    <p>&copy; 2024 Example Research Inc.</p>
    <p>For permission requests contact permissions@example-research.test.</p>
  </footer>
  </main>
</body>
</html>
"""

JSONLD_ONLY_PAGE = """<html><head>
  <script type="application/ld+json">
    [{"@type": "TechArticle", "headline": "Late Chunking Reconsidered",
      "datePublished": "2023-06-01", "dateModified": "2023-07-01",
      "author": [{"@type": "Person", "name": "Bob Lin"}, {"@type": "Person", "name": "Ada Wu"}]}]
  </script>
</head><body><p>Body text only.</p></body></html>
"""

TWITTER_ONLY_PAGE = """<html><head>
  <meta name="twitter:title" content="Twitter Card Title">
  <meta name="twitter:description" content="Twitter card description text.">
</head><body><p>Hello.</p></body></html>
"""

PLAIN_TEXT = "Gateway routes requests to shard pools."


# ---------------------------------------------------------------------------
# MockTransport site
# ---------------------------------------------------------------------------


class FakeSite:
    """A tiny in-memory website: pages, robots, redirect, hit counters."""

    def __init__(self) -> None:
        main_page = "https://research.test/papers/deep-rl"
        self.pages: dict[str, tuple[int, str, str]] = {
            "https://research.test/robots.txt": (200, "text/plain", ROBOTS_TXT),
            main_page: (200, "text/html; charset=utf-8", RESEARCH_PAGE),
            "https://research.test/index": (
                200,
                "text/html",
                '<html><body><a href="/a">A</a> <a href="/a#frag">A2</a>'
                ' <a href="/b">B</a> <a href="https://other.test/x">X</a></body></html>',
            ),
            "https://research.test/a": (
                200,
                "text/html",
                '<html><body><a href="/deep">deep</a></body></html>',
            ),
            "https://research.test/b": (
                200,
                "text/html",
                "<html><body><p>B page</p></body></html>",
            ),
            "https://research.test/deep": (
                200,
                "text/html",
                "<html><body><p>Deep page</p></body></html>",
            ),
            "https://research.test/private/area": (
                200,
                "text/html",
                "<html><body>shh</body></html>",
            ),
            "https://research.test/plain.txt": (200, "text/plain", "not html"),
            "https://research.test/lamin": (
                200,
                "text/html; charset=iso-8859-1",
                "<html><body><p>caf\xe9 latt\xe9</p></body></html>",
            ),
        }
        self.robots_hits = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://research.test/robots.txt":
            self.robots_hits += 1
        if url == "https://research.test/old":
            return httpx.Response(302, headers={"Location": "https://research.test/papers/deep-rl"})
        entry = self.pages.get(url)
        if entry is None:
            return httpx.Response(404, headers={"content-type": "text/plain"}, text="missing")
        status, content_type, body = entry
        # Encode with the DECLARED charset so the header-driven decode on the
        # client side is actually exercised (a text= response would re-encode
        # everything as UTF-8).
        charset = content_type.split("charset=")[-1] if "charset=" in content_type else "utf-8"
        return httpx.Response(
            status, headers={"content-type": content_type}, content=body.encode(charset)
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _profile(path: str = "https://research.test/papers/deep-rl") -> SourceProfile:
    return SourceProfile(source_type="web", is_file=False, path=path)


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


async def test_fetch_page_returns_post_redirect_fields() -> None:
    site = FakeSite()
    page = await fetch_page(
        "https://research.test/old", WebFetchConfig(), transport=site.transport()
    )
    assert page.status_code == 200
    assert page.final_url == "https://research.test/papers/deep-rl"
    assert "Corpus Curation" in page.html
    assert page.content_type.startswith("text/html")
    assert page.fetched_at  # ISO timestamp string


async def test_fetch_page_decodes_charset_from_headers() -> None:
    site = FakeSite()
    page = await fetch_page(
        "https://research.test/lamin", WebFetchConfig(), transport=site.transport()
    )
    assert "café latté" in page.html


async def test_fetch_page_rejects_non_html_content_type() -> None:
    site = FakeSite()
    with pytest.raises(WebFetchError, match="content-type"):
        await fetch_page("https://research.test/plain.txt", WebFetchConfig(), site.transport())


async def test_fetch_page_enforces_byte_size_cap() -> None:
    site = FakeSite()
    config = WebFetchConfig(max_bytes=64)
    with pytest.raises(WebFetchError, match="size-cap"):
        await fetch_page("https://research.test/papers/deep-rl", config, site.transport())


async def test_fetch_page_honors_robots_disallow() -> None:
    site = FakeSite()
    with pytest.raises(WebFetchError, match="robots-disallowed"):
        await fetch_page("https://research.test/private/area", WebFetchConfig(), site.transport())


async def test_fetch_page_respects_respect_robots_false() -> None:
    site = FakeSite()
    config = WebFetchConfig(respect_robots=False)
    page = await fetch_page("https://research.test/private/area", config, site.transport())
    assert page.status_code == 200
    assert site.robots_hits == 0


async def test_robots_is_fetched_once_per_crawl_and_allows_other_paths() -> None:
    site = FakeSite()
    config = WebFetchConfig(max_pages=5, max_depth=2, delay_seconds=0.0)
    pages = await crawl("https://research.test/index", config, transport=site.transport())
    assert site.robots_hits == 1
    assert pages  # the allow path: robots permitted every fetched page
    allowed = await fetch_page(
        "https://research.test/papers/deep-rl", WebFetchConfig(), site.transport()
    )
    assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


async def test_crawl_caps_pages_and_dedupes() -> None:
    site = FakeSite()
    config = WebFetchConfig(max_pages=3, max_depth=2, delay_seconds=0.0)
    pages = await crawl("https://research.test/index", config, transport=site.transport())
    urls = [normalize_url(page.url) for page in pages]
    assert len(pages) == 3
    assert urls == [
        "https://research.test/index",
        "https://research.test/a",
        "https://research.test/b",
    ]


async def test_crawl_respects_max_depth() -> None:
    site = FakeSite()
    config = WebFetchConfig(max_pages=10, max_depth=1, delay_seconds=0.0)
    pages = await crawl("https://research.test/index", config, transport=site.transport())
    urls = {normalize_url(page.url) for page in pages}
    assert "https://research.test/deep" not in urls  # depth 2 > max_depth 1
    assert "https://research.test/a" in urls


async def test_crawl_stays_on_registrable_domain() -> None:
    site = FakeSite()
    config = WebFetchConfig(max_pages=10, max_depth=2, delay_seconds=0.0)
    pages = await crawl("https://research.test/index", config, transport=site.transport())
    assert all("other.test" not in page.url for page in pages)


def test_normalize_url_strips_fragments_and_keeps_query() -> None:
    assert normalize_url("https://Example.test/A#section") == "https://example.test/A"
    assert normalize_url("https://example.test/A?x=1#frag") == "https://example.test/A?x=1"


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------


def _page_for(html: str, url: str = "https://research.test/papers/deep-rl") -> FetchedPage:
    return FetchedPage(
        url=url, final_url=url, status_code=200, html=html, content_type="text/html", fetched_at=""
    )


def test_extract_metadata_full_page_all_keys() -> None:
    meta = extract_metadata(_page_for(RESEARCH_PAGE))
    assert set(meta) == set(METADATA_KEYS)
    assert meta["title"] == "Deep Reinforcement Learning for Corpus Curation | Example Research"
    assert meta["description"] == "A study of RL-based corpus curation pipelines."
    assert meta["site_name"] == "Example Research"
    assert meta["author"] == "Maria Chen"
    assert meta["published_time"] == "2024-03-01T08:00:00Z"
    assert meta["modified_time"] == "2024-04-10T09:30:00Z"
    assert meta["language"] == "en"
    assert meta["canonical"] == "https://research.test/papers/deep-rl"


def test_extract_metadata_jsonld_fills_absent_meta() -> None:
    meta = extract_metadata(_page_for(JSONLD_ONLY_PAGE))
    assert meta["title"] == "Late Chunking Reconsidered"
    assert meta["published_time"] == "2023-06-01"
    assert meta["modified_time"] == "2023-07-01"
    assert meta["author"] == "Bob Lin, Ada Wu"
    assert meta["description"] is None  # JSON-LD never invents keys beyond the contract


def test_extract_metadata_meta_wins_over_jsonld() -> None:
    meta = extract_metadata(_page_for(RESEARCH_PAGE))
    assert meta["title"] != "JSONLD Headline Should Lose"
    assert meta["author"] != "Wrong Author"


def test_extract_metadata_twitter_fallback_when_og_missing() -> None:
    meta = extract_metadata(_page_for(TWITTER_ONLY_PAGE))
    assert meta["title"] == "Twitter Card Title"
    assert meta["description"] == "Twitter card description text."


def test_extract_metadata_all_none_on_unparsable_html() -> None:
    meta = extract_metadata(_page_for(PLAIN_TEXT))
    assert meta == dict.fromkeys(METADATA_KEYS)


# ---------------------------------------------------------------------------
# WebStructureStrategy
# ---------------------------------------------------------------------------


def test_strategy_detect_claim_and_refusal() -> None:
    strategy = WebStructureStrategy()
    assert strategy.detect("web", "x", "").reason == "web-source-type"
    assert strategy.detect("web", "x", "").confidence == 1.0
    assert strategy.detect("url", "x", "").confidence == 1.0
    assert strategy.detect(None, "https://x.test/a", "").confidence == 1.0
    assert strategy.detect("markdown", "doc.md", "").confidence == 0.0


def test_strategy_sections_carry_heading_breadcrumbs() -> None:
    h1 = "Deep Reinforcement Learning for Corpus Curation"
    plan = WebStructureStrategy().build_chunks(RESEARCH_PAGE, "deep-rl", _profile())
    paths = [chunk.heading_path for chunk in plan.chunks]
    assert [h1] in paths
    assert [h1, "Background", "Related Work"] in paths
    assert [h1, "Methodology"] in paths
    # Every breadcrumb starts at the h1 (depth 0 resets the path).
    assert all(path and path[0] == h1 for path in paths)


def test_strategy_chunks_carry_web_metadata_and_element_types() -> None:
    plan = WebStructureStrategy().build_chunks(RESEARCH_PAGE, "deep-rl", _profile())
    assert plan.chunks
    for chunk in plan.chunks:
        assert chunk.source_type == "text"
        assert (
            chunk.metadata["title"]
            == "Deep Reinforcement Learning for Corpus Curation | Example Research"
        )
        assert chunk.metadata["site_name"] == "Example Research"
        assert chunk.metadata["author"] == "Maria Chen"
        assert chunk.metadata["published_time"] == "2024-03-01T08:00:00Z"
        assert chunk.metadata["url"] == "https://research.test/papers/deep-rl"
        assert chunk.metadata["final_url"] == "https://research.test/papers/deep-rl"
        assert chunk.metadata["language"] == "en"
        assert isinstance(chunk.metadata["element_types"], list)
        assert chunk.metadata["element_types"]


def test_strategy_renders_tables_as_github_markdown() -> None:
    plan = WebStructureStrategy().build_chunks(RESEARCH_PAGE, "deep-rl", _profile())
    results = [
        chunk
        for chunk in plan.chunks
        if chunk.heading_path is not None and chunk.heading_path[-1] == "Results"
    ]
    assert results, "Results section chunk missing"
    text = "\n".join(chunk.text for chunk in results)
    assert "| Pipeline | F1 |" in text
    assert "| --- | --- |" in text
    assert "| rl-curator | 0.84 |" in text
    assert any("Table" in chunk.metadata["element_types"] for chunk in results)


def test_strategy_drops_short_chrome_keeps_substantive_footer() -> None:
    assert CHROME_MIN_TEXT_CHARS == 40
    plan = WebStructureStrategy().build_chunks(RESEARCH_PAGE, "deep-rl", _profile())
    all_text = "\n".join(chunk.text for chunk in plan.chunks)
    assert "© 2024 Example Research Inc." not in all_text  # short chrome dropped
    assert "permissions@example-research.test" in all_text  # substantive footer kept
    assert "© 2024 Example Research Inc." not in plan.document_text
    footer_chunks = [
        chunk
        for chunk in plan.chunks
        if "Footer" in cast("list[str]", chunk.metadata["element_types"])
    ]
    assert footer_chunks and {"Header", "Footer"} == CHROME_CATEGORIES


def test_strategy_validate_plan_holds_on_arbitrary_html() -> None:
    strategy = WebStructureStrategy()
    inputs = [
        RESEARCH_PAGE,
        "",
        PLAIN_TEXT,
        "<html><body><p>unclosed",
        "x" * (TOKEN_BUDGET_CHARS * 2),
        "<table><tr><td>wide</td></tr></table>",
    ]
    for raw in inputs:
        plan = strategy.build_chunks(raw, "page", _profile())
        assert validate_plan(plan) == [], f"non-conformant plan for {raw[:40]!r}"


def test_strategy_empty_extraction_sets_empty_reason() -> None:
    plan = WebStructureStrategy().build_chunks("", "page", _profile())
    assert plan.chunks == []
    assert plan.empty_reason is not None
    assert validate_plan(plan) == []


def test_strategy_constructor_metadata_overrides_extraction() -> None:
    override: dict[str, str | None] = {"title": "Overridden", "author": None}
    plan = WebStructureStrategy(metadata=override).build_chunks(
        RESEARCH_PAGE, "deep-rl", _profile("https://other.test/x")
    )
    assert plan.chunks
    for chunk in plan.chunks:
        assert chunk.metadata["title"] == "Overridden"
        assert chunk.metadata["author"] is None
        assert chunk.metadata["url"] == "https://other.test/x"  # setdefault fill


def test_pack_elements_respects_budget_and_paragraph_boundaries() -> None:
    from corpus_kb.partitioning import ElementProxy

    paragraphs = [f"para {index} " + "y" * 30 for index in range(10)]
    elements = [
        ElementProxy(text=text, element_type="NarrativeText", element_id=str(index))
        for index, text in enumerate(paragraphs)
    ]
    budget = 100
    groups = pack_elements(elements, budget)
    assert groups
    for group in groups:
        joined = "\n\n".join(element.text for element in group)
        assert len(joined) <= budget


# ---------------------------------------------------------------------------
# config_from_mapping
# ---------------------------------------------------------------------------


def test_config_from_mapping_reads_web_section() -> None:
    config = config_from_mapping(
        {
            "user_agent": "bot/1.0",
            "timeout_seconds": 5,
            "max_bytes": 1024,
            "respect_robots": False,
            "max_pages": 2,
            "max_depth": 1,
            "delay_seconds": 0.5,
        }
    )
    assert config == WebFetchConfig(
        user_agent="bot/1.0",
        timeout_seconds=5,
        max_bytes=1024,
        respect_robots=False,
        max_pages=2,
        max_depth=1,
        delay_seconds=0.5,
    )


def test_config_from_mapping_falls_back_to_defaults() -> None:
    assert config_from_mapping(None) == WebFetchConfig()
    assert config_from_mapping({"timeout_seconds": "fast"}) == WebFetchConfig()
    assert config_from_mapping({"max_pages": 3}).max_pages == 3


# ---------------------------------------------------------------------------
# ingest_url end-to-end (stub store + fake pool + MockTransport)
# ---------------------------------------------------------------------------


_INGEST_CONFIG: dict[str, object] = {
    "graph": {"extract_entities": False},
    "embedding": {"provider": "pgml"},
    "ingest": {"hierarchy": {"enabled": False}},
    "web": {
        "user_agent": TEST_AGENT,
        "timeout_seconds": 5,
        "max_bytes": 1048576,
        "respect_robots": True,
        "max_pages": 5,
        "max_depth": 1,
        "delay_seconds": 0.0,
    },
}


class _StubStore:
    """Minimal PostgresIngestStore stand-in: no chunks inserted, no errors."""

    def __init__(self, pool: object, tenant_id: str = "t") -> None:
        del pool, tenant_id

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        return None

    async def begin_run(self, document: object, file_hash: str) -> tuple[str, str]:
        return "doc-1", "run-1"

    async def store_chunks(self, chunks: list[object]) -> set[str]:
        return set()

    async def store_vectors(
        self,
        chunks: list[object],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        return 0

    async def store_entities(self, entities: list[object]) -> int:
        return 0

    async def store_relations(
        self, relations: list[object], model_version: object = None, prompt_version: object = None
    ) -> int:
        return 0

    async def update_document(self, document: object, file_hash: str) -> None:
        return None

    async def finish_run(
        self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
    ) -> None:
        return None

    async def fail_run(self, run_id: str, error: str) -> None:
        return None


def _fake_pool() -> asyncpg.Pool:
    return cast("asyncpg.Pool", _FakePool(_LockConn(acquired=True)))


async def test_ingest_url_end_to_end_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools.ingest_tools import ingest_url

    result = await ingest_url(
        "https://research.test/papers/deep-rl",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        crawl=False,
        transport=site.transport(),
    )
    assert result["status"] == "success"
    assert result["total_documents"] == 1
    pages = cast("list[dict[str, Any]]", result["pages"])
    assert len(pages) == 1
    page = pages[0]
    assert page["status"] == "success"
    assert page["strategy"] == WEB_STRATEGY_NAME
    assert page["strategy_basis"] == "detection"
    assert page["detection_reason"] == "web-source-type"
    assert page["chunk_count"] > 0
    assert page["path"] == "https://research.test/papers/deep-rl"


async def test_ingest_url_crawl_ingests_linked_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools.ingest_tools import ingest_url

    result = await ingest_url(
        "https://research.test/index",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        crawl=True,
        transport=site.transport(),
    )
    assert result["status"] == "success"
    pages = cast("list[dict[str, Any]]", result["pages"])
    assert {page.get("path") for page in pages} >= {
        "https://research.test/index",
        "https://research.test/a",
        "https://research.test/b",
    }
    assert all(page.get("status") == "success" for page in pages)
    assert "https://other.test/x" not in {page.get("path") for page in pages}


async def test_ingest_url_fetch_error_is_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools.ingest_tools import ingest_url

    missing = await ingest_url(
        "https://research.test/gone", _fake_pool(), dict(_INGEST_CONFIG), transport=site.transport()
    )
    assert missing["status"] == "error"
    assert "http-404" in str(missing["message"])

    denied = await ingest_url(
        "https://research.test/private/area",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        transport=site.transport(),
    )
    assert denied["status"] == "error"
    assert "robots-disallowed" in str(denied["message"])

    non_html = await ingest_url(
        "https://research.test/plain.txt",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        transport=site.transport(),
    )
    assert non_html["status"] == "error"
    assert "content-type" in str(non_html["message"])


async def test_ingest_url_per_page_failure_never_aborts_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools import ingest_url_tools

    async def exploding_pipeline(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("store boom")

    monkeypatch.setattr(ingest_url_tools, "run_pipeline", exploding_pipeline)
    result = await ingest_url_tools.ingest_url(
        "https://research.test/index",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        crawl=True,
        transport=site.transport(),
    )
    assert result["status"] == "success"
    pages = cast("list[dict[str, Any]]", result["pages"])
    assert len(pages) >= 2
    assert all(page["status"] == "error" and "store boom" in str(page["message"]) for page in pages)
    assert result["total_documents"] == 0


async def test_ingest_url_rejects_unknown_strategy_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools.ingest_tools import ingest_url

    result = await ingest_url(
        "https://research.test/papers/deep-rl",
        _fake_pool(),
        dict(_INGEST_CONFIG),
        source_type="bogus",
        transport=site.transport(),
    )
    assert result["status"] == "error"
    assert "Unknown source-type strategy" in str(result["message"])


async def test_ingest_url_uses_web_config_section_for_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite()
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", _StubStore)
    from corpus_kb.tools.ingest_tools import ingest_url

    config = dict(_INGEST_CONFIG)
    config["web"] = {**_INGEST_CONFIG["web"], "respect_robots": False, "max_pages": 2}
    result = await ingest_url(
        "https://research.test/private/area",
        _fake_pool(),
        config,
        crawl=True,
        transport=site.transport(),
    )
    # respect_robots: false lets the disallowed path through; max_pages caps the crawl.
    assert result["status"] == "success"
    pages = cast("list[dict[str, Any]]", result["pages"])
    assert len(pages) == 1
    assert site.robots_hits == 0


def test_ingest_url_json_safety_of_metadata() -> None:
    plan = WebStructureStrategy().build_chunks(RESEARCH_PAGE, "deep-rl", _profile())
    for chunk in plan.chunks:
        encoded = json.dumps(chunk.metadata)
        assert "NaN" not in encoded


# ---------------------------------------------------------------------------
# Surface parity: CLI command + HTTP route
# ---------------------------------------------------------------------------


def test_cli_ingest_url_help_lists_flags() -> None:
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    result = CliRunner().invoke(app, ["ingest-url", "--help"])
    assert result.exit_code == 0
    for flag in ("--crawl", "--max-pages", "--source-type", "--json"):
        assert flag in result.output


def test_cli_ingest_url_unknown_strategy_exits_config_error() -> None:
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    result = CliRunner().invoke(
        app, ["ingest-url", "https://research.test/a", "--source-type", "bogus"]
    )
    assert result.exit_code == 3
    assert "unknown source-type strategy 'bogus'" in result.output.lower()


def test_http_app_registers_ingest_url_route() -> None:
    from corpus_kb.api.http import create_http_app

    paths = {route.path for route in create_http_app().routes}
    assert "/api/ingest/url" in paths

"""Complex research ingestion E2E: deck + website through the REAL pipeline.

Scenario: a research project on grounded evaluation of retrieval-augmented
systems ingests a three-part corpus through the production front doors —

  1. a 10-slide methodology deck with a comparison table and speaker notes
     (``pptx_structure`` strategy via the ``ingest_file`` entry),
  2. a multi-section research article page (``web_structure`` via
     ``ingest_url``, single page),
  3. the article's companion appendix reached by same-domain crawl.

The Postgres store is a recording fake (full Chunk/Document capture, real
ChunkPlan conformance upstream) and the embedder is patched at the
``OllamaEmbedder.embed_batch`` method seam (zero vectors stay fatal by house
policy, so a dead service cannot yield a success run to assert on): strategy
resolution, chunk building, embedder selection, and the honesty envelope
(``embedding_mode`` always, ``embedding_note`` when late chunking was
requested but unavailable) all remain real pipeline code. No network, no
database, no Ollama.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import asyncpg
import httpx
import pytest
from test_ingest_integrity import _FakePool, _LockConn

from corpus_kb.tools.ingest_tools import ingest_file, ingest_url
from corpus_kb.utils.models import Chunk

RESEARCH_HOST = "https://research.test"

ARTICLE_PATH = "/papers/grounded-eval"
APPENDIX_PATH = "/papers/grounded-eval/appendix"

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Grounded Evaluation of Retrieval-Augmented Coding Agents</title>
  <meta name="description" content="A three-stage grounded evaluation of RAG coding agents.">
  <link rel="canonical" href="{base}{path}">
  <meta property="og:title" content="Grounded Evaluation of Retrieval-Augmented Coding Agents">
  <meta property="og:description" content="A three-stage grounded evaluation of RAG coding agents.">
  <meta property="og:site_name" content="Journal of Retrieval Practice">
  <script type="application/ld+json">
  {"@context": "https://schema.org", "@type": "ScholarlyArticle",
    "headline": "Grounded Evaluation of Retrieval-Augmented Coding Agents",
    "datePublished": "2026-09-30",
    "author": {"@type": "Person", "name": "Dr. Grace Hopper"}}
  </script>
</head>
<body>
  <nav><a href="/">Home</a><a href="/papers">Papers</a></nav>
  <main>
    <h1>Grounded Evaluation of Retrieval-Augmented Coding Agents</h1>
    <h2>Abstract</h2>
    <p>We evaluate retrieval-augmented coding agents with a three-stage
    grounded protocol: corpus construction, hybrid retrieval benchmarking,
    and claim-by-claim verdict coding against a recorded codebook.</p>
    <h2>Method</h2>
    <p>The corpus spans source trees, interview transcripts, slide decks, and
    web pages. Each modality is chunked by its own structure-aware strategy
    before a shared hybrid retrieval layer fuses lexical and vector legs.</p>
    <h3>Corpus construction</h3>
    <p>Documents are hashed, deduplicated, and tracked in an ingest ledger so
    re-runs are idempotent and degraded embeddings are discoverable.</p>
    <h2>Results</h2>
    <p>Structure-aware chunking beat fixed-window chunking on every modality.
    The table summarizes recall and latency.</p>
    <table>
      <tr><th>Approach</th><th>Recall@10</th><th>p95 latency (ms)</th></tr>
      <tr><td>Fixed window</td><td>0.62</td><td>41</td></tr>
      <tr><td>Heading sections</td><td>0.81</td><td>38</td></tr>
    </table>
    <blockquote><p>As one participant put it: the deck knew what the paper
    only implied.</p></blockquote>
    <h2>Threats to Validity</h2>
    <p>Single-corpus scope, local embedding drift across model upgrades, and
    a small rater pool bound the claims. The appendix lists mitigations.</p>
    <p>The full protocol continues in the
    <a href="/papers/grounded-eval/appendix">appendix</a>.</p>
  </main>
</body>
</html>
"""

APPENDIX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Appendix: Protocol Mitigations</title>
  <meta name="description" content="Mitigations for the grounded evaluation protocol.">
  <link rel="canonical" href="{base}{path}">
</head>
<body>
  <main>
    <h1>Appendix: Protocol Mitigations</h1>
    <h2>Embedding drift</h2>
    <p>Every chunk records its embedding model and dimensions; a re-embed
    pass migrates degraded vectors without touching chunk text.</p>
    <h2>Rater pool</h2>
    <p>Reliability gates (Krippendorff alpha floors, audit sampling) gate
    codebook promotion; disagreement routes to human review.</p>
  </main>
</body>
</html>
"""


def _site_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == ARTICLE_PATH:
            return httpx.Response(
                200,
                content=ARTICLE_HTML.replace("{base}", RESEARCH_HOST).replace(
                    "{path}", ARTICLE_PATH
                ),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if path == APPENDIX_PATH:
            return httpx.Response(
                200,
                content=APPENDIX_HTML.replace("{base}", RESEARCH_HOST).replace(
                    "{path}", APPENDIX_PATH
                ),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if path == "/robots.txt":
            return httpx.Response(
                200,
                content="User-agent: *\nAllow: /\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


class _RecordingStore:
    """PostgresIngestStore stand-in that captures what the pipeline wrote."""

    def __init__(self, pool: object, tenant_id: str = "") -> None:
        del pool, tenant_id
        self.documents: list[dict[str, Any]] = []
        self.chunks_by_doc: dict[str, list[Chunk]] = {}
        self.finished: list[tuple[str, list[str]]] = []

    async def unchanged_doc_id(self, path: str, file_hash: str) -> None:
        del path, file_hash
        return None

    async def begin_run(self, document: Any, file_hash: str) -> tuple[str, str]:
        del file_hash
        return f"doc-{len(self.documents) + 1}", f"run-{len(self.documents) + 1}"

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        for chunk in chunks:
            self.chunks_by_doc.setdefault(str(chunk.document_id), []).append(chunk)
        return {str(chunk.chunk_id) for chunk in chunks}

    async def store_vectors(
        self,
        chunks: list[object],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: object = None,
    ) -> int:
        del chunks, embedding_model, dimensions, vectors_1024
        return 0

    async def store_entities(self, entities: list[object]) -> int:
        del entities
        return 0

    async def store_relations(
        self, relations: list[object], model_version: object = None, prompt_version: object = None
    ) -> int:
        del relations, model_version, prompt_version
        return 0

    async def update_document(self, document: Any, file_hash: str) -> None:
        del file_hash
        self.documents.append(
            {
                "path": str(getattr(document, "path", "")),
                "source_type": getattr(document, "source_type", ""),
            }
        )

    async def finish_run(
        self, run_id: str, file_hash: str, stages: dict[str, object], errors: list[str]
    ) -> None:
        del file_hash, stages
        self.finished.append((run_id, list(errors)))

    async def fail_run(self, run_id: str, error: str) -> None:
        self.finished.append((run_id, [error]))


def _config() -> dict[str, object]:
    return {
        "graph": {"extract_entities": False},
        "ingest": {"chunking": {"enabled": False}},
        "contextual": {"enabled": False},
        "embedding": {
            "provider": "pgml",
            "fallback_provider": "ollama",
            "model": "nomic-embed-text",
            "base_url": "http://localhost:65432",  # dead port: deterministic degraded fallback
            "dimensions": 768,
            "batch_size": 32,
            "late_chunking": True,
        },
        "ingestion": {
            "strategies": {"pptx": "pptx_structure", "web": "web_structure"},
            "default": "default",
        },
        "web": {
            "user_agent": "corpus-kb-e2e/0.1",
            "timeout_seconds": 5,
            "max_bytes": 1048576,
            "respect_robots": True,
            "max_pages": 5,
            "max_depth": 2,
            "delay_seconds": 0.0,
        },
    }


def _harness(monkeypatch: pytest.MonkeyPatch) -> _RecordingStore:
    """Recording store + embedder method seam.

    The store records everything the pipeline writes; ``OllamaEmbedder.embed_batch``
    is patched to deterministic non-zero vectors so the REAL embed_chunks /
    select_ingest_embedder path runs offline. Zero vectors stay fatal by house
    policy (they would poison the search corpus), so the dead-port degraded
    path cannot produce a success run to assert on — the seam keeps the run
    succeedable while selection and the honesty envelope stay real code.
    """
    from corpus_kb.rag.embedder import OllamaEmbedder
    from corpus_kb.tools import ingest_common

    def fake_embed_batch(self: OllamaEmbedder, texts: list[str]) -> list[list[float]]:
        del self
        return [[0.1 + (len(text) % 7) / 100.0] * 768 for text in texts]

    store = _RecordingStore(object(), "")
    monkeypatch.setattr(ingest_common, "PostgresIngestStore", lambda pool, tenant_id="": store)
    monkeypatch.setattr(OllamaEmbedder, "embed_batch", fake_embed_batch)
    return store


def _fake_pool() -> asyncpg.Pool:
    return cast("asyncpg.Pool", _FakePool(_LockConn(acquired=True)))


def _assert_honest_pooled_envelope(result: dict[str, Any]) -> None:
    """Late chunking requested (default) but unavailable here: honest note."""
    assert result["status"] == "success"
    assert result["embedding_mode"] == "pooled"
    note = result.get("embedding_note")
    assert isinstance(note, str) and note, "fallback must be explained, never silent"
    assert "latechunk extra" in note or "token-level" in note


# ---------------------------------------------------------------------------
# 1. Methodology deck through ingest_file (pptx_structure)
# ---------------------------------------------------------------------------


async def test_deck_ingestion_preserves_slide_structure_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptx_factory import DECK_AUTHOR, DECK_TITLE, TABLE_TITLE, make_research_deck

    store = _harness(monkeypatch)
    deck_path = make_research_deck(tmp_path / "grounded_rag_survey.pptx")

    result = await ingest_file(str(deck_path), _fake_pool(), _config())

    _assert_honest_pooled_envelope(result)
    assert result["strategy"] == "pptx_structure"
    assert result["strategy_basis"] == "config"
    assert result["chunk_count"] >= 10

    chunks = next(iter(store.chunks_by_doc.values()))
    slide_numbers = {
        chunk.metadata["slide_number"]
        for chunk in chunks
        if isinstance(chunk.metadata.get("slide_number"), int)
        and "slide_notes" not in chunk.metadata
    }
    assert slide_numbers == set(range(1, 11))

    body = [chunk for chunk in chunks if "slide_notes" not in chunk.metadata]
    assert all(chunk.metadata["deck_title"] == DECK_TITLE for chunk in chunks)
    assert all(chunk.metadata["author"] == DECK_AUTHOR for chunk in chunks)
    assert all(
        chunk.heading_path is not None and chunk.heading_path[0] == DECK_TITLE for chunk in body
    )

    table_chunks = [chunk for chunk in body if chunk.metadata.get("slide_title") == TABLE_TITLE]
    assert table_chunks, "comparison table slide must produce chunks"
    assert any("|" in chunk.text for chunk in table_chunks), "table must render as markdown"

    notes = [chunk for chunk in chunks if "slide_notes" in chunk.metadata]
    assert notes, "speaker notes must be ingested"
    assert all(chunk.metadata["has_notes"] is True for chunk in notes)

    assert store.documents[0]["source_type"] == "pptx"
    assert all(errors == [] for _run, errors in store.finished)


# ---------------------------------------------------------------------------
# 2. Research article through ingest_url (web_structure, single page)
# ---------------------------------------------------------------------------


async def test_article_ingestion_preserves_sections_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _harness(monkeypatch)

    result = await ingest_url(
        f"{RESEARCH_HOST}{ARTICLE_PATH}",
        _fake_pool(),
        _config(),
        transport=_site_transport(),
    )

    assert result["status"] == "success"
    assert result["total_documents"] == 1
    page = cast("list[dict[str, Any]]", result["pages"])[0]
    _assert_honest_pooled_envelope(page)
    assert page["strategy"] == "web_structure"

    chunks = next(iter(store.chunks_by_doc.values()))
    assert all(chunk.metadata["canonical"] == f"{RESEARCH_HOST}{ARTICLE_PATH}" for chunk in chunks)
    assert all(chunk.metadata["language"] == "en" for chunk in chunks)
    assert all(chunk.metadata["site_name"] == "Journal of Retrieval Practice" for chunk in chunks)
    # JSON-LD fallback: author/published_time exist only in the ld+json block.
    assert all(chunk.metadata["author"] == "Dr. Grace Hopper" for chunk in chunks)
    assert all(chunk.metadata["published_time"] == "2026-09-30" for chunk in chunks)

    results_chunks = [
        chunk
        for chunk in chunks
        if chunk.heading_path and any("Results" in part for part in chunk.heading_path)
    ]
    assert results_chunks, "Results section must carry its heading breadcrumb"
    assert any("0.81" in chunk.text for chunk in results_chunks), "results table data must survive"

    method_chunks = [
        chunk
        for chunk in chunks
        if chunk.heading_path and any(part == "Corpus construction" for part in chunk.heading_path)
    ]
    assert method_chunks, "h3 subsection must nest under its breadcrumb"

    assert store.documents[0]["source_type"] == "web"
    assert all(errors == [] for _run, errors in store.finished)


# ---------------------------------------------------------------------------
# 3. Crawl: article + linked appendix in one call
# ---------------------------------------------------------------------------


async def test_crawl_reaches_the_linked_appendix(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _harness(monkeypatch)

    result = await ingest_url(
        f"{RESEARCH_HOST}{ARTICLE_PATH}",
        _fake_pool(),
        _config(),
        crawl=True,
        transport=_site_transport(),
    )

    assert result["status"] == "success"
    assert result["total_documents"] == 2
    assert {doc["source_type"] for doc in store.documents} == {"web"}
    paths = {doc["path"] for doc in store.documents}
    assert paths == {f"{RESEARCH_HOST}{ARTICLE_PATH}", f"{RESEARCH_HOST}{APPENDIX_PATH}"}

    appendix_chunks = [
        chunk
        for chunks in store.chunks_by_doc.values()
        for chunk in chunks
        if chunk.metadata.get("canonical") == f"{RESEARCH_HOST}{APPENDIX_PATH}"
    ]
    assert appendix_chunks, "appendix page must be ingested with its own canonical URL"
    assert any("Krippendorff" in chunk.text for chunk in appendix_chunks)


# ---------------------------------------------------------------------------
# 4. Whole research corpus in one knowledge base
# ---------------------------------------------------------------------------


async def test_whole_research_corpus_coexists_in_one_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pptx_factory import make_research_deck

    store = _harness(monkeypatch)
    pool = _fake_pool()
    cfg = _config()
    deck_path = make_research_deck(tmp_path / "survey.pptx")

    deck_result = await ingest_file(str(deck_path), pool, cfg)
    article_result = await ingest_url(
        f"{RESEARCH_HOST}{ARTICLE_PATH}", pool, cfg, transport=_site_transport()
    )

    assert deck_result["status"] == "success"
    assert article_result["status"] == "success"
    assert len(store.documents) == 2

    deck_chunks = [
        chunk
        for chunks in store.chunks_by_doc.values()
        for chunk in chunks
        if "slide_number" in chunk.metadata
    ]
    web_chunks = [
        chunk
        for chunks in store.chunks_by_doc.values()
        for chunk in chunks
        if "canonical" in chunk.metadata
    ]
    assert deck_chunks and web_chunks, "both metadata vocabularies must coexist"
    # Every chunk stayed inside the pipeline's conformance budget (validate_plan
    # already enforced this upstream; recorded here as the cross-strategy pin).
    from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS

    assert all(len(chunk.text) <= TOKEN_BUDGET_CHARS for chunk in deck_chunks + web_chunks)


# ---------------------------------------------------------------------------
# 5. The flagship conjunction: late chunking ENGAGES for strategy chunks
# ---------------------------------------------------------------------------


async def test_late_chunking_engages_on_real_strategy_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strategy-produced spans must flow into the late path, not fall back.

    This is the integration the whole wave exists for: with the activation
    contract satisfied (stubbed at select_ingest_embedder — the extras-less
    CI never installs the latechunk stack), BOTH new strategies' chunks are
    embedded from spans into their own document text. Runs the deck path;
    the web path is covered by the same span contract in test_web_ingestion.
    """
    from test_late_chunk_ingest import FakeTokenEmbedder, _spans_provider

    from corpus_kb.rag import embedder_selection
    from corpus_kb.rag.embedder_selection import LATE_CHUNKING_MODE, IngestEmbeddingPlan
    from corpus_kb.tools import ingest_embed

    fake = FakeTokenEmbedder()
    store = _harness(monkeypatch)
    received_spans: list[list[tuple[int, int]]] = []

    def recording_spans_provider(
        document_text: str, chunk_char_spans: list[tuple[int, int]]
    ) -> list[Any]:
        received_spans.append(list(chunk_char_spans))
        return _spans_provider(fake)(document_text, chunk_char_spans)

    plan = IngestEmbeddingPlan(
        mode=LATE_CHUNKING_MODE, embedder=fake, spans_provider=recording_spans_provider
    )
    monkeypatch.setattr(embedder_selection, "_late_embedder_singleton", None)
    monkeypatch.setattr(ingest_embed, "select_ingest_embedder", lambda config, pool=None: plan)

    from pptx_factory import make_research_deck

    deck_path = make_research_deck(tmp_path / "survey_late.pptx")
    result = await ingest_file(str(deck_path), _fake_pool(), _config())

    assert result["status"] == "success"
    assert result["embedding_mode"] == LATE_CHUNKING_MODE, "the marquee path must engage"
    chunks = next(iter(store.chunks_by_doc.values()))
    assert received_spans, "spans provider must have been called"
    assert len(received_spans[0]) == len(chunks), (
        "every strategy chunk must carry a trusted span (no silent pooled fallback)"
    )
    assert all(chunk.embedding for chunk in chunks), "late vectors must be populated"
    # Late-chunking property across the real deck: identical chunk TEXTS at
    # different slide positions got different vectors.
    texts: dict[str, list[float]] = {}
    for chunk in chunks:
        if chunk.text in texts:
            assert chunk.embedding != texts[chunk.text], (
                "identical text at a different position must embed differently"
            )
        texts[chunk.text] = chunk.embedding

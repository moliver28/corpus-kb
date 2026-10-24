"""HTTP adapter — FastAPI REST API for Corpus-KB.

Composition root only: it builds the FastAPI app (a Starlette subclass,
so plain starlette.routing.Route objects and the TestClient are
unchanged) from the per-family route modules (zero-drift split of the
former single-file route table).

Thin adapters: parse JSON → construct Pydantic command/query →
call handler → return JSONResponse. No business logic here.

Every route is wrapped in ``@route_errors`` (api._body): ONE error path.
A ``CorpusError`` answers with its own status; a known exception class
answers through ``errors.ERROR_MAP``; anything else is a 500
``internal_error``. The failure envelope is always
``{status, error, error_type}`` — never a bare 400.

Route families (the Route order below is the original single-file order):
  api/routes_ingest.py         POST /api/ingest/{file,text,directory,url}
                               GET /api/ingest/status  POST /api/ingest/reembed
  api/routes_search.py         POST /api/search, /api/search/similar,
                               /api/search/context, /api/verify, /api/query,
                               /api/query/sql, /api/embed
  api/routes_ask.py            POST /api/ask
  api/routes_coding.py         POST /api/coding/* (six routes)
  api/routes_meta.py           GET /api/documents, /api/entities, /api/versions,
                               /api/stats, /api/tables, /api/document-stats
  api/routes_graph.py          POST /api/entities, /api/relations
                               DELETE /api/documents/{doc_id}
                               POST /api/graph/{search,bfs}
                               GET  /api/graph/relations/{entity_id}
  api/routes_tags_metadata.py  POST /api/tags, /api/metadata  GET /api/metadata
                               POST+GET /api/documents/{doc_id}/tags
  api/research_http.py         research + release surfaces (U5/P7)
  api/routes_topics.py         POST /api/topics/build  GET /api/topics
                               POST /api/topics/trends
  api/routes_labeling.py       POST /api/labeling/build  GET /api/labeling
                               GET  /api/labeling/lfs
  api/routes_classification.py POST /api/classification/build  GET /api/classification
  api/routes_analysis.py       POST /api/analysis/backfill-dates  POST /api/analyze
  api/routes_analysis_methods.py  POST /api/analysis/{select-method,
                               keyphrases-extract, lexical-classify,
                               neighbors-classify, centroids-classify,
                               clusters-inspect, duplicates-screen,
                               outliers-rank, nli-classify,
                               contradictions-screen, sentiment-screen,
                               active-learn-next}  (AMR program)
  api/routes_v1.py             OpenAI-compatible /v1/* (gate: server.openai_compat)
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import BaseRoute, Route

from corpus_kb.api.research_http import research_routes
from corpus_kb.api.routes_analysis import (
    analysis_backfill_dates_route,
    analyze_run_route,
)
from corpus_kb.api.routes_analysis_methods import (
    active_learn_next_route,
    analysis_select_method_route,
    centroids_classify_route,
    clusters_inspect_route,
    contradictions_screen_route,
    duplicates_screen_route,
    keyphrases_extract_route,
    lexical_classify_route,
    neighbors_classify_route,
    nli_classify_route,
    outliers_rank_route,
    sentiment_screen_route,
)
from corpus_kb.api.routes_ask import ask
from corpus_kb.api.routes_beliefs import (
    beliefs_compare_route,
    beliefs_current_route,
    beliefs_predict_route,
    beliefs_timeline_route,
)
from corpus_kb.api.routes_classification import (
    classification_build_route,
    classification_list_route,
)
from corpus_kb.api.routes_coding import (
    coding_calibrate_floors,
    coding_code_batch,
    coding_embed_codebook,
    coding_pool,
    coding_reliability,
    coding_saturation,
)
from corpus_kb.api.routes_conflicts import (
    conflicts_list_route,
    conflicts_resolve_route,
    conflicts_scan_route,
)
from corpus_kb.api.routes_graph import (
    add_entity,
    add_relation,
    bfs_traversal,
    delete_document,
    get_entity_relations,
    graph_neighbors,
    graph_traverse,
    search_graph,
)
from corpus_kb.api.routes_ingest import (
    ingest_directory,
    ingest_file,
    ingest_reembed_route,
    ingest_status_route,
    ingest_text,
    ingest_url_route,
)
from corpus_kb.api.routes_labeling import (
    labeling_build_route,
    labeling_lfs_route,
    labeling_list_route,
)
from corpus_kb.api.routes_meta import (
    document_stats,
    get_stats,
    list_documents,
    list_entities,
    list_versions,
    sql_tables,
)
from corpus_kb.api.routes_meta_embed import analysis_embed_meta_route
from corpus_kb.api.routes_search import (
    embed_text,
    query_sql,
    route_query,
    search,
    search_context,
    search_similar,
    verify,
)
from corpus_kb.api.routes_statements import (
    statements_list_route,
    statements_timeline_route,
)
from corpus_kb.api.routes_tags_metadata import (
    add_tag_route,
    get_document_tags_route,
    get_metadata_route,
    set_metadata_route,
    tag_document_route,
)
from corpus_kb.api.routes_topics import (
    topics_build_route,
    topics_list_route,
    topics_trends_route,
)
from corpus_kb.api.routes_tune import extraction_tune_route
from corpus_kb.api.routes_v1 import v1_routes


def create_http_app() -> FastAPI:
    """Create the HTTP application.

    FastAPI subclasses Starlette, so every plain ``starlette.routing.Route``
    (and the TestClient, and uvicorn) keeps working unchanged. The extra
    default routes it adds (/docs, /openapi.json) are additive; existing
    tests assert route subsets, not equality.
    """
    routes: list[BaseRoute] = [
        Route("/api/ingest/file", ingest_file, methods=["POST"]),
        Route("/api/ingest/text", ingest_text, methods=["POST"]),
        Route("/api/ingest/directory", ingest_directory, methods=["POST"]),
        Route("/api/ingest/url", ingest_url_route, methods=["POST"]),
        # Stage-5 ingest front door: status + reembed surfaces.
        Route("/api/ingest/status", ingest_status_route, methods=["GET"]),
        Route("/api/ingest/reembed", ingest_reembed_route, methods=["POST"]),
        # Wave 4 topics layer.
        Route("/api/topics/build", topics_build_route, methods=["POST"]),
        Route("/api/topics", topics_list_route, methods=["GET"]),
        # Analysis program: temporal topic trends (WP-4).
        Route("/api/topics/trends", topics_trends_route, methods=["POST"]),
        # Analysis program Wave 0: labeling, classification, backfill, run.
        Route("/api/labeling/build", labeling_build_route, methods=["POST"]),
        Route("/api/labeling", labeling_list_route, methods=["GET"]),
        Route("/api/labeling/lfs", labeling_lfs_route, methods=["GET"]),
        Route("/api/classification/build", classification_build_route, methods=["POST"]),
        Route("/api/classification", classification_list_route, methods=["GET"]),
        Route("/api/analysis/backfill-dates", analysis_backfill_dates_route, methods=["POST"]),
        Route("/api/analyze", analyze_run_route, methods=["POST"]),
        # Analysis method register program (wave 2): the method ops.
        Route("/api/analysis/select-method", analysis_select_method_route, methods=["POST"]),
        Route("/api/analysis/keyphrases-extract", keyphrases_extract_route, methods=["POST"]),
        Route("/api/analysis/lexical-classify", lexical_classify_route, methods=["POST"]),
        Route("/api/analysis/neighbors-classify", neighbors_classify_route, methods=["POST"]),
        Route("/api/analysis/centroids-classify", centroids_classify_route, methods=["POST"]),
        Route("/api/analysis/clusters-inspect", clusters_inspect_route, methods=["POST"]),
        Route("/api/analysis/duplicates-screen", duplicates_screen_route, methods=["POST"]),
        Route("/api/analysis/outliers-rank", outliers_rank_route, methods=["POST"]),
        Route("/api/analysis/nli-classify", nli_classify_route, methods=["POST"]),
        Route("/api/analysis/contradictions-screen", contradictions_screen_route, methods=["POST"]),
        Route("/api/analysis/sentiment-screen", sentiment_screen_route, methods=["POST"]),
        Route("/api/analysis/active-learn-next", active_learn_next_route, methods=["POST"]),
        # Living Corpus W6: the meta-embedding build op.
        Route("/api/analysis/embed-meta", analysis_embed_meta_route, methods=["POST"]),
        # Living Corpus W2: the statement-atom read ops.
        Route("/api/statements/list", statements_list_route, methods=["POST"]),
        Route("/api/statements/timeline", statements_timeline_route, methods=["POST"]),
        # Living Corpus W3: the conflict ops.
        Route("/api/conflicts/scan", conflicts_scan_route, methods=["POST"]),
        Route("/api/conflicts/list", conflicts_list_route, methods=["POST"]),
        Route("/api/conflicts/resolve", conflicts_resolve_route, methods=["POST"]),
        # Living Corpus W4: the belief ops.
        Route("/api/beliefs/current", beliefs_current_route, methods=["POST"]),
        Route("/api/beliefs/timeline", beliefs_timeline_route, methods=["POST"]),
        Route("/api/beliefs/predict", beliefs_predict_route, methods=["POST"]),
        # Living Corpus W5: the tune op.
        Route("/api/extraction-tune", extraction_tune_route, methods=["POST"]),
        Route("/api/beliefs/compare", beliefs_compare_route, methods=["POST"]),
        Route("/api/search", search, methods=["POST"]),
        Route("/api/search/similar", search_similar, methods=["POST"]),
        Route("/api/search/context", search_context, methods=["POST"]),
        Route("/api/verify", verify, methods=["POST"]),
        Route("/api/query", route_query, methods=["POST"]),
        Route("/api/query/sql", query_sql, methods=["POST"]),
        Route("/api/embed", embed_text, methods=["POST"]),
        # UX Wave 1 ask operation: the grounded answer front door.
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/coding/embed-codebook", coding_embed_codebook, methods=["POST"]),
        Route("/api/coding/pool", coding_pool, methods=["POST"]),
        Route("/api/coding/code-batch", coding_code_batch, methods=["POST"]),
        Route("/api/coding/reliability", coding_reliability, methods=["POST"]),
        Route("/api/coding/calibrate-floors", coding_calibrate_floors, methods=["POST"]),
        Route("/api/coding/saturation", coding_saturation, methods=["POST"]),
        Route("/api/documents", list_documents, methods=["GET"]),
        Route("/api/entities", list_entities, methods=["GET"]),
        Route("/api/entities", add_entity, methods=["POST"]),
        Route("/api/relations", add_relation, methods=["POST"]),
        Route("/api/documents/{doc_id}", delete_document, methods=["DELETE"]),
        Route("/api/graph/search", search_graph, methods=["POST"]),
        Route("/api/graph/bfs", bfs_traversal, methods=["POST"]),
        Route("/api/graph/neighbors", graph_neighbors, methods=["POST"]),
        Route("/api/graph/traverse", graph_traverse, methods=["POST"]),
        Route("/api/graph/relations/{entity_id}", get_entity_relations, methods=["GET"]),
        Route("/api/tags", add_tag_route, methods=["POST"]),
        Route("/api/documents/{doc_id}/tags", tag_document_route, methods=["POST"]),
        Route("/api/documents/{doc_id}/tags", get_document_tags_route, methods=["GET"]),
        Route("/api/metadata", set_metadata_route, methods=["POST"]),
        Route("/api/metadata", get_metadata_route, methods=["GET"]),
        Route("/api/versions", list_versions, methods=["GET"]),
        Route("/api/stats", get_stats, methods=["GET"]),
        Route("/api/tables", sql_tables, methods=["GET"]),
        Route("/api/document-stats", document_stats, methods=["GET"]),
        # Research + release surfaces (U5/P7) — routes defined in
        # api/research_http.py over the shared tool layer.
        *research_routes(),
        # OpenAI-compatible /v1 adapter (UX Wave 2) — routes defined in
        # api/routes_v1.py over the ask tool + embedder; empty tuple when
        # server.openai_compat is off.
        *v1_routes(),
    ]

    app = FastAPI(routes=routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return app

"""Spec rows for the mechanical socket arms (pure data).

One row per pool-and-call operation: the lazy import target, where the
research pool goes, and the strict coercion per parameter (issue #238
helpers). ``socket_ops`` turns these rows into arms once at import; this
module holds no behavior.
"""

from __future__ import annotations

from corpus_kb.api._socket_specs import _Opt, _Spec

# Lazy-import targets for the spec rows (one short name per tool module).
_INGEST = "corpus_kb.tools.ingest_tools"
_INGEST_S = "corpus_kb.tools.ingest_surface_tools"
_TOPICS = "corpus_kb.tools.topics_tools"
_LABEL = "corpus_kb.tools.labeling_tools"
_GOLD = "corpus_kb.tools.labeling_gold_tools"
_CLASS = "corpus_kb.tools.classification_tools"
_ANALYSIS = "corpus_kb.tools.analysis_tools"
_STMT = "corpus_kb.tools.statements_tools"
_CONF = "corpus_kb.tools.conflicts_tools"
_QUEUE = "corpus_kb.tools.queue_tools"
_BELIEFS = "corpus_kb.tools.beliefs_tools"
_KEY = "corpus_kb.tools.keyphrase_tools"
_LEX = "corpus_kb.tools.lexical_tools"
_NEIGH = "corpus_kb.tools.neighbor_tools"
_CENT = "corpus_kb.tools.centroid_tools"
_CLUST = "corpus_kb.tools.cluster_tools"
_VSCREEN = "corpus_kb.tools.vector_screen_tools"
_NLI = "corpus_kb.tools.nli_tools"
_SENT = "corpus_kb.tools.sentiment_tools"
_ACTIVE = "corpus_kb.tools.active_learn_tools"
_MEMBED = "corpus_kb.tools.meta_embed_tools"
_ASK = "corpus_kb.tools.ask_tools"
_GRAPH = "corpus_kb.tools.graph_read_tools"

# Mechanical arms: pool fetch + strict coercion, said once in _spec_arm.
_SPECS: dict[str, _Spec] = {
    "ingest_directory": _Spec(
        _INGEST,
        "ingest_directory",
        pool_pos=1,
        positional=(_Opt("directory_path", "req"),),
        opts=(_Opt("source_type", "raw"), _Opt("recursive", "bool", True), _Opt("validate", "str")),
    ),
    "ingest_url": _Spec(
        _INGEST,
        "ingest_url",
        pool_pos=1,
        positional=(_Opt("url", "req"),),
        opts=(_Opt("crawl", "bool", False), _Opt("source_type", "raw"), _Opt("validate", "str")),
    ),
    "ingest_status": _Spec(
        _INGEST_S, "ingest_status", opts=(_Opt("limit", "int", 20), _Opt("project_id", "str"))
    ),
    "ingest_reembed": _Spec(
        _INGEST_S, "ingest_reembed", opts=(_Opt("limit", "int"), _Opt("doc_id", "raw"))
    ),
    "delete_document": _Spec(
        _INGEST, "delete_document", pool_pos=1, positional=(_Opt("document_id", "req"),)
    ),
    "topics_build": _Spec(_TOPICS, "topics_build"),
    "topics_list": _Spec(_TOPICS, "topics_list", opts=(_Opt("limit", "int", 50),)),
    "topics_trends": _Spec(_TOPICS, "topics_trends", opts=(_Opt("bins", "int"),)),
    "labeling_build": _Spec(_LABEL, "labeling_build"),
    "labeling_list": _Spec(_LABEL, "labeling_list", opts=(_Opt("limit", "int", 50),)),
    "labeling_lfs": _Spec(_LABEL, "labeling_lfs", opts=(_Opt("limit", "int", 50),)),
    "labeling_gold_check": _Spec(
        _GOLD,
        "labeling_gold_check",
        opts=(_Opt("gold_file", "sdef", ""), _Opt("confidence_level", "float", 0.95)),
    ),
    "classification_build": _Spec(_CLASS, "classification_build", opts=(_Opt("seed_file", "str"),)),
    "classification_list": _Spec(_CLASS, "classification_list", opts=(_Opt("limit", "int", 50),)),
    "analysis_backfill_dates": _Spec(_ANALYSIS, "analysis_backfill_dates"),
    "analyze_run": _Spec(_ANALYSIS, "analyze_run", opts=(_Opt("output", "str"),)),
    "keyphrases_extract": _Spec(
        _KEY,
        "keyphrases_extract",
        opts=(_Opt("variant", "sdef", "tfidf"), _Opt("limit", "int", 20), _Opt("chunk_ids", "raw")),
    ),
    "lexical_classify": _Spec(_LEX, "lexical_classify", opts=(_Opt("chunk_ids", "raw"),)),
    "neighbors_classify": _Spec(
        _NEIGH, "neighbors_classify", opts=(_Opt("k", "int", 5), _Opt("chunk_ids", "raw"))
    ),
    "centroids_classify": _Spec(
        _CENT,
        "centroids_classify",
        opts=(_Opt("abstain_margin", "float", 0.05), _Opt("chunk_ids", "raw")),
    ),
    "clusters_inspect": _Spec(
        _CLUST,
        "clusters_inspect",
        opts=(_Opt("min_cluster_size", "int"), _Opt("sample_cap", "int")),
    ),
    "duplicates_screen": _Spec(
        _VSCREEN,
        "duplicates_screen",
        opts=(_Opt("threshold", "float", 0.98), _Opt("limit", "int", 100)),
    ),
    "outliers_rank": _Spec(
        _VSCREEN, "outliers_rank", opts=(_Opt("k", "int", 10), _Opt("limit", "int", 20))
    ),
    "contradictions_screen": _Spec(
        _NLI,
        "contradictions_screen",
        opts=(_Opt("candidate_k", "int", 5), _Opt("limit", "int", 50), _Opt("model", "str")),
    ),
    "sentiment_screen": _Spec(
        _SENT, "sentiment_screen", opts=(_Opt("chunk_ids", "raw"), _Opt("limit", "int"))
    ),
    "active_learn_next": _Spec(
        _ACTIVE,
        "active_learn_next",
        opts=(_Opt("strategy", "sdef", "margin"), _Opt("batch", "int", 10)),
    ),
    "analysis_embed_meta": _Spec(_MEMBED, "analysis_embed_meta", opts=(_Opt("kinds", "str"),)),
    "statements_list": _Spec(
        _STMT,
        "statements_list",
        opts=(_Opt("template_id", "str"), _Opt("document_id", "str"), _Opt("limit", "int", 50)),
    ),
    "statements_timeline": _Spec(
        _STMT, "statements_timeline", opts=(_Opt("text_query", "str"), _Opt("limit", "int", 200))
    ),
    "conflicts_scan": _Spec(_CONF, "conflicts_scan"),
    "conflicts_list": _Spec(
        _CONF, "conflicts_list", opts=(_Opt("verdict", "str"), _Opt("limit", "int", 50))
    ),
    "conflicts_resolve": _Spec(
        _CONF,
        "conflicts_resolve",
        opts=(
            _Opt("conflict_id", "sdef", ""),
            _Opt("resolved_verdict", "sdef", ""),
            _Opt("resolved_by", "sdef", ""),
        ),
    ),
    "beliefs_current": _Spec(
        _BELIEFS,
        "beliefs_current",
        opts=(_Opt("subject", "str"), _Opt("limit", "int", 50), _Opt("rebuild", "bool", True)),
    ),
    "beliefs_timeline": _Spec(
        _BELIEFS,
        "beliefs_timeline",
        opts=(_Opt("subject", "str"), _Opt("relation", "str"), _Opt("object", "str")),
    ),
    "beliefs_predict": _Spec(
        _BELIEFS,
        "beliefs_predict",
        opts=(_Opt("subject", "str"), _Opt("relation", "str"), _Opt("object", "str")),
    ),
    "beliefs_compare": _Spec(_BELIEFS, "beliefs_compare", opts=(_Opt("subject", "str"),)),
    "queue_enqueue": _Spec(
        _QUEUE,
        "queue_enqueue",
        opts=(
            _Opt("path", "sdef", ""),
            _Opt("title", "str"),
            _Opt("profile", "str"),
            _Opt("strategy_config", "raw"),
            _Opt("project_id", "str"),
        ),
    ),
    "queue_status": _Spec(
        _QUEUE, "queue_status", opts=(_Opt("queue_id", "str"), _Opt("limit", "int", 50))
    ),
    "queue_cancel": _Spec(_QUEUE, "queue_cancel", opts=(_Opt("queue_id", "sdef", ""),)),
    "ask": _Spec(
        _ASK,
        "ask",
        pool_kw="pg_pool",
        positional=(_Opt("query", "sdef", ""),),
        opts=(_Opt("k", "int"),),
    ),
    "search_graph": _Spec(
        _GRAPH,
        "search_graph",
        positional=(_Opt("query", "sdef", ""),),
        opts=(_Opt("entity_type", "raw"), _Opt("limit", "int", 100)),
    ),
    "get_entity_relations": _Spec(
        _GRAPH,
        "get_entity_relations",
        positional=(_Opt("entity_id", "req"),),
        opts=(_Opt("limit", "int"),),
    ),
    "graph_neighbors": _Spec(
        _GRAPH,
        "graph_neighbors",
        positional=(_Opt("entity_id", "req"),),
        opts=(
            _Opt("hops", "int", 1),
            _Opt("direction", "sdef", "both"),
            _Opt("relation_types", "raw"),
            _Opt("entity_types", "raw"),
            _Opt("limit", "int", 100),
        ),
    ),
    "graph_traverse": _Spec(
        _GRAPH,
        "graph_traverse",
        positional=(_Opt("entity_id", "req"),),
        opts=(
            _Opt("max_depth", "int"),
            _Opt("relation_types", "raw"),
            _Opt("entity_types", "raw"),
            _Opt("limit", "int", 100),
        ),
    ),
}

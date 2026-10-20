"""The operation catalogue: the topics family (Wave 4 + analysis W0).

``topics_build``/``topics_list`` run the offline BERTopic layer over the
STORED chunk embeddings. ``topics_build`` is honest on every outcome the
feature has: the dormant default (``topics.enabled: false``) is a no-op
envelope naming the flag, a pre-migration database is ``unavailable``,
and a missing ``[topics]`` extra raises the typed dependency error (HTTP
503 / socket -32005 / CLI exit 3). ``topics_trends`` is the temporal
layer (analysis program WP-4): per-topic frequency series over binned
document dates, dormant behind ``analysis.topics.over_time.enabled``.
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

# Guaranteed-on-success keys only: the disabled/unavailable reasons and
# the build's counts/params are outcome-specific and stay off the tuple
# (the envelopes.py rule).
TOPICS_ENVELOPE = ("status", "topic_count", "topics")
TOPICS_TRENDS_ENVELOPE = ("status", "topic_count", "trends")

TOPICS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="topics_build",
        family="topics",
        description=(
            "Cluster the stored chunk embeddings into topics (offline "
            "BERTopic). Honest no-op while topics.enabled is false."
        ),
        handler="corpus_kb.tools.topics_tools:topics_build",
        params=(_TENANT,),
        envelope_keys=TOPICS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "topics_build"),
            SurfaceSpec(CLI, "topics-build"),
            SurfaceSpec(HTTP, "/api/topics/build", "POST"),
            SurfaceSpec(SOCKET, "topics_build"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="topics_list",
        family="topics",
        description="List the built topics with sizes, labels, and keywords.",
        handler="corpus_kb.tools.topics_tools:topics_list",
        params=(ParamSpec("limit", default=50), _TENANT),
        envelope_keys=TOPICS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "topics_list"),
            SurfaceSpec(CLI, "topics-list"),
            SurfaceSpec(HTTP, "/api/topics", "GET"),
            SurfaceSpec(SOCKET, "topics_list"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="topics_trends",
        family="topics",
        description=(
            "Per-topic frequency series over binned document dates "
            "(topics over time). Honest no-op while "
            "analysis.topics.over_time.enabled is false."
        ),
        handler="corpus_kb.tools.topics_tools:topics_trends",
        params=(ParamSpec("bins", default=None), _TENANT),
        envelope_keys=TOPICS_TRENDS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "topics_trends"),
            SurfaceSpec(CLI, "topics-trends"),
            SurfaceSpec(HTTP, "/api/topics/trends", "POST"),
            SurfaceSpec(SOCKET, "topics_trends"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)

"""Graph write policy (textbook-ingestion program, Wave 2).

The ONE policy that decides which extracted relations reach the graph
store. ``mode: all`` (the default) writes every relation, byte for byte
like the pre-policy pipeline. ``mode: semantic_only`` keeps only the
relation types the ontology marks ``graph_eligible`` and drops the rest,
in stable input order. An absent flag means eligible, so the shipped
ontology changes nothing.

Pure functions only. The pipeline's extract stage is the single call
site; CLI/HTTP/socket/MCP stay thin adapters over the same write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corpus_kb.ontology import Ontology
    from corpus_kb.utils.models import Relation

GRAPH_POLICY_MODES = ("all", "semantic_only")
DEFAULT_GRAPH_POLICY_MODE = "all"


def graph_policy_mode(config: dict[str, object]) -> str:
    """Read ``graph_policy.mode``; anything else reads as the default."""
    policy = config.get("graph_policy")
    if not isinstance(policy, dict):
        return DEFAULT_GRAPH_POLICY_MODE
    mode = policy.get("mode")
    if mode not in GRAPH_POLICY_MODES:
        return DEFAULT_GRAPH_POLICY_MODE
    return str(mode)


def graph_eligible_relations(
    relations: list[Relation],
    ontology: Ontology,
) -> list[Relation]:
    """The relations the policy admits to the graph store, order stable."""
    return [
        relation
        for relation in relations
        if ontology.relation_is_graph_eligible(relation.relation_type)
    ]

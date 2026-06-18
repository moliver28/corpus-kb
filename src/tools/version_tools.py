"""MCP tools for versioning, branching, and tags.

Tools:
- list_versions: Show all table versions
- create_tag: Tag a version for reference
- time_travel: Query at a specific version
- get_stats: Database statistics
"""

from __future__ import annotations

from storage.lancedb_store import LanceDBStore
from storage.graph_store import GraphStore


def register_tools(mcp, store: LanceDBStore, graph: GraphStore):
    """Register all version/tag tools with the MCP server."""

    @mcp.tool()
    def list_versions() -> list[dict]:
        """List all versions of the chunks table for time-travel.

        Returns:
            List of version entries with version number, timestamp, and tag.
        """
        versions = store.list_versions()
        return [
            {
                "version": v.version,
                "timestamp": v.timestamp,
                "tag": v.tag,
            }
            for v in versions
        ]

    @mcp.tool()
    def create_tag(version: int, tag_name: str) -> dict:
        """Tag a specific version for reference.

        Args:
            version: Version number to tag.
            tag_name: Human-readable tag name (e.g., "v1.0", "before-refactor").

        Returns:
            Confirmation with version and tag name.
        """
        store.create_tag(version, tag_name)
        return {"version": version, "tag": tag_name, "status": "created"}

    @mcp.tool()
    def get_stats() -> dict:
        """Get database statistics.

        Returns:
            Stats object with counts and storage info.
        """
        stats = store.get_stats()
        graph_stats = graph.get_stats()
        return {
            "total_documents": stats.total_documents,
            "total_chunks": stats.total_chunks,
            "total_entities": graph_stats.get("entity_count", 0),
            "total_relations": graph_stats.get("relation_count", 0),
            "current_version": stats.current_version,
            "storage_path": stats.storage_path,
        }

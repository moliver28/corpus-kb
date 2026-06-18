"""MCP tools for versioning, branching, and tags.

Tools:
- list_versions: Show all table versions
- create_tag: Tag a version for reference
- time_travel: Query at a specific version
- get_stats: Database statistics
"""

from __future__ import annotations

from typing import Optional

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
            "total_entities": graph_stats.get("total_entities", 0),
            "total_relations": graph_stats.get("total_relations", 0),
            "current_version": stats.current_version,
            "storage_path": stats.storage_path,
        }

    @mcp.tool()
    async def checkout_version(version: int) -> dict:
        """Check out a specific table version for time-travel queries."""
        try:
            store.checkout(version)
            return {"version": version, "status": "checked_out"}
        except Exception as e:
            return {"version": version, "status": "error", "message": str(e)}

    @mcp.tool()
    async def restore_version(version: int) -> dict:
        """Restore the table to a specific version."""
        try:
            store.restore(version)
            return {"version": version, "status": "restored"}
        except Exception as e:
            return {"version": version, "status": "error", "message": str(e)}

    @mcp.tool()
    async def create_branch(branch_name: str, from_version: Optional[int] = None) -> dict:
        """Create a new branch from an optional version."""
        try:
            store.create_branch(branch_name, from_version)
            return {"branch": branch_name, "from_version": from_version, "status": "created"}
        except Exception as e:
            return {"branch": branch_name, "status": "error", "message": str(e)}

    @mcp.tool()
    async def list_branches() -> list[dict]:
        """List all branches."""
        try:
            branches = store.list_branches()
            return [{"name": b} for b in branches]
        except Exception as e:
            return [{"status": "error", "message": str(e)}]

    @mcp.tool()
    async def switch_branch(branch_name: str) -> dict:
        """Switch to a specified branch."""
        try:
            store.switch_branch(branch_name)
            return {"branch": branch_name, "status": "switched"}
        except Exception as e:
            return {"branch": branch_name, "status": "error", "message": str(e)}

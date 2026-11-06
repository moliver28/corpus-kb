"""The operation catalogue: the project-table family (Track C).

Three ops over the per-project raw tables: ``project_provision`` (create
``raw_<slug>`` + its registry row), ``project_list`` (the registry rows
with live row counts), ``project_drop`` (drop the table and its row).
The physical tables are the ONE sanctioned dynamic-DDL seam
(``storage/project_tables.py``); the registry migration enumerates them
so every gate sees them. Registry-first rule: change the row first, then
the surfaces, then the gates. All strings are plain English (ASD-STE100).
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

PROVISION_ENVELOPE = ("status", "table", "registry_id", "backend", "already_provisioned")
LIST_ENVELOPE = ("status", "entries", "count")
DROP_ENVELOPE = ("status", "table", "dropped_rows")

PROJECT_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="project_provision",
        family="project",
        description=(
            "Create the raw table for one project slug. Idempotent: a "
            "repeat provision answers the existing registry row."
        ),
        handler="corpus_kb.tools.project_tools:project_provision",
        params=(
            ParamSpec("slug", required=True),
            ParamSpec("project_id"),
            _TENANT,
        ),
        envelope_keys=PROVISION_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "project_provision"),
            SurfaceSpec(CLI, "project-provision"),
            SurfaceSpec(HTTP, "/api/project/provision", "POST"),
            SurfaceSpec(SOCKET, "project_provision"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="project_list",
        family="project",
        description=(
            "List the provisioned project raw tables with a live row "
            "count for each. A missing table answers row_count null."
        ),
        handler="corpus_kb.tools.project_tools:project_list",
        params=(_TENANT,),
        envelope_keys=LIST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "project_list"),
            SurfaceSpec(CLI, "project-list"),
            SurfaceSpec(HTTP, "/api/project/list", "POST"),
            SurfaceSpec(SOCKET, "project_list"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="project_drop",
        family="project",
        description=(
            "Drop one project raw table and its registry row in one "
            "transaction. The envelope states how many rows were lost."
        ),
        handler="corpus_kb.tools.project_tools:project_drop",
        params=(
            ParamSpec("slug", required=True),
            _TENANT,
        ),
        envelope_keys=DROP_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "project_drop"),
            SurfaceSpec(CLI, "project-drop"),
            SurfaceSpec(HTTP, "/api/project/drop", "POST"),
            SurfaceSpec(SOCKET, "project_drop"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)

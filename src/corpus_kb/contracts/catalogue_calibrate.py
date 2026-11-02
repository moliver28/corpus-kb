"""The operation catalogue: the calibrate family (issue #200).

``calibrate_apply`` turns a profile lock's ``proposed_overrides`` into
config.yaml values. The sweep writes the lock; the apply op is the one
seam that copies the proven values in, with a diff before any write.
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import CLI, HTTP, MCP, SOCKET, Operation, ParamSpec, SurfaceSpec

CALIBRATE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="calibrate_apply",
        family="calibrate",
        description="Copy a profile lock's proposed overrides into config.yaml.",
        handler="corpus_kb.tools.calibrate_tools:calibrate_apply",
        params=(
            ParamSpec("profile", required=True),
            ParamSpec("write", default=False),
        ),
        envelope_keys=("status", "result"),
        surfaces=(
            SurfaceSpec(MCP, "calibrate_apply"),
            SurfaceSpec(CLI, "calibrate apply"),
            SurfaceSpec(HTTP, "/api/calibrate/apply", "POST"),
            SurfaceSpec(SOCKET, "calibrate_apply"),
        ),
        exit_codes=(0, 1, 3),
    ),
)

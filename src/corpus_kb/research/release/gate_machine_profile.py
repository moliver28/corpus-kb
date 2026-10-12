"""Machine-profile release gate (U10 completeness + U34 panel diversity).

Pure functions over ``(GateInputs, ResolvedProfile)``; the manifest block is
assembled by ``gate_reads.py``. Honesty rule: a gate never upgrades missing
evidence into a pass.

U10 machine_profile — every machine-profile field present; missing fields
are ``not_evaluable`` when no required profile demands the gate and ``fail``
when it does (v6: "not_evaluable or fail per profile").

U34 coder-panel diversity rides here: proposer and validator must come from
DIFFERENT model families (``fail`` in high-assurance, warn-reason
elsewhere). The family mapping is a HEURISTIC literal prefix table over the
model ids the repo actually runs; unknown ids never prove diversity.
"""

from __future__ import annotations

from corpus_kb.research.release.gate_support import as_dict, as_str
from corpus_kb.research.release.gate_types import (
    GATE_MACHINE_PROFILE,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import PROFILE_HIGH_ASSURANCE, ResolvedProfile

# Machine-profile fields the manifest must record (v6 section 6 U10 list).
REQUIRED_MACHINE_PROFILE_FIELDS: tuple[str, ...] = (
    "model_id",
    "model_digest",
    "runtime_endpoint",
    "model_family",
    "prompt_bundle_sha256",
    "decoding_params",
    "seeds",
    "embedder_id",
    "embedding_dimensions",
    "app_version",
    "package_versions",
)

# U34 model-family mapping. HEURISTIC: a small literal prefix table over the
# model ids the repo actually runs (qwen/llama/gemma/... local Ollama names
# and hosted ids). Unknown ids map to "unknown" and never prove diversity.
_MODEL_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("qwen", "qwen"),
    ("llama", "llama"),
    ("gemma", "gemma"),
    ("mistral", "mistral"),
    ("mixtral", "mistral"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("claude", "anthropic"),
    ("deepseek", "deepseek"),
    ("phi", "microsoft-phi"),
    ("command", "cohere"),
    ("granite", "ibm-granite"),
    ("glm", "zhipu-glm"),
    ("nomic", "nomic"),
)


def model_family(model_id: str) -> str:
    """Heuristic family for a model id (prefix table, else ``unknown``)."""
    lowered = model_id.strip().lower()
    for prefix, family in _MODEL_FAMILY_PREFIXES:
        if lowered.startswith(prefix) or f"/{prefix}" in lowered or f":{prefix}" in lowered:
            return family
    return "unknown"


def _panel_block(profile_block: dict[str, object]) -> dict[str, object]:
    """Normalized coder-panel view with heuristic families filled in."""
    panel = as_dict(profile_block.get("coder_panel"))
    view: dict[str, object] = {}
    for role in ("proposer", "validator"):
        role_block = as_dict(panel.get(role))
        model_id = as_str(role_block.get("model_id"))
        family = as_str(role_block.get("family")) or (model_family(model_id) if model_id else None)
        view[role] = {"model_id": model_id, "family": family}
    proposer = as_dict(view.get("proposer"))
    validator = as_dict(view.get("validator"))
    fam_a = as_str(proposer.get("family"))
    fam_b = as_str(validator.get("family"))
    if not fam_a or not fam_b or "unknown" in (fam_a, fam_b):
        diverse: bool | None = None
    else:
        diverse = fam_a != fam_b
    view["panel_diverse"] = diverse
    view["family_mapping"] = "heuristic prefix table (gate_machine_profile._MODEL_FAMILY_PREFIXES)"
    return view


def gate_machine_profile(inputs: GateInputs, profile: ResolvedProfile) -> GateResult:
    """U10 (+U34): manifest machine profile complete; panel diversity recorded.

    Missing required fields -> ``fail`` when this profile REQUIRES the gate,
    ``not_evaluable`` otherwise (v6 U10). Panel diversity: proposer and
    validator must come from different model families — ``fail`` in
    high-assurance, a warn-prefixed pass reason elsewhere.
    """
    manifest = as_dict(inputs.manifest)
    block = as_dict(manifest.get("machine_profile")) if "machine_profile" in manifest else manifest
    if not block or as_str(block.get("model_id")) is None:
        return GateResult(
            gate_id=GATE_MACHINE_PROFILE,
            status="not_evaluable",
            reason="no machine profile recorded (inputs.manifest carries none)",
        )
    missing = [f for f in REQUIRED_MACHINE_PROFILE_FIELDS if block.get(f) in (None, "", {}, [])]
    panel = _panel_block(block)
    diverse = panel.get("panel_diverse")
    required = GATE_MACHINE_PROFILE in profile.required_gates
    if missing and not required:
        return GateResult(
            gate_id=GATE_MACHINE_PROFILE,
            status="not_evaluable",
            reason=f"machine profile incomplete (missing: {missing})",
            value={"missing": missing, "coder_panel": panel},
        )
    reasons: list[str] = []
    status = "pass"
    if missing:
        status = "fail"
        reasons.append(f"machine profile incomplete (missing: {missing})")
    if diverse is not True:
        note = (
            "coder panel NOT family-diverse (proposer and validator share a family)"
            if diverse is False
            else "coder-panel family diversity unverifiable (family unknown/unrecorded)"
        )
        if profile.name == PROFILE_HIGH_ASSURANCE:
            status = "fail"
            reasons.append(f"U34: {note}")
        else:
            reasons.append(f"warn: {note}")
    if not reasons:
        reasons.append("machine profile complete; coder panel family-diverse")
    return GateResult(
        gate_id=GATE_MACHINE_PROFILE,
        status=status,
        reason="; ".join(reasons),
        value={"missing": missing, "coder_panel": panel},
        threshold={
            "required_fields": list(REQUIRED_MACHINE_PROFILE_FIELDS),
            "panel_diversity_enforced_in": PROFILE_HIGH_ASSURANCE,
        },
    )


__all__ = [
    "REQUIRED_MACHINE_PROFILE_FIELDS",
    "gate_machine_profile",
    "model_family",
]

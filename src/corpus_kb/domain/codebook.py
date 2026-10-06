"""CodebookVersion aggregate (todo-11 (a)) — ONE instance PER PROMOTION.

Events: Created, CodeAdded, DefinitionRefined, PrototypeUpdated,
ThresholdRecalibrated, KeywordSetUpdated. A version's full state is the
replay of its own event chain; the NEXT promotion mints a NEW aggregate
instance (immutable version history).

PrototypeUpdated carries exemplar text_sha256 REFS, never vectors — vectors
are derived data rebuilt through the embedding cache.

TENANT CONTRACT (r8): every event signature carries tenant_id explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from eventsourcing.domain import Aggregate, event

MAX_CODES_PER_EVENT = 100
MAX_KEYWORDS_PER_EVENT = 200


@dataclass
class CodebookVersion(Aggregate):
    """One codebook version's authoritative event chain."""

    tenant_id: UUID
    label: str
    sha256: str
    paradigm: str = "deductive"
    notes: str = ""
    codes: dict[str, dict[str, object]] = field(default_factory=dict)

    @event("Created")
    def __init__(
        self,
        tenant_id: UUID,
        label: str,
        sha256: str,
        paradigm: str = "deductive",
        notes: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.label = label
        self.sha256 = sha256
        self.paradigm = paradigm
        self.notes = notes
        self.codes = {}

    @event("CodeAdded")
    def add_codes(self, tenant_id: UUID, codes: list[dict[str, object]]) -> None:
        """Add one batch of codes; each carries a caller-minted code_id."""
        if not 0 < len(codes) <= MAX_CODES_PER_EVENT:
            raise ValueError(f"code batch must be 1..{MAX_CODES_PER_EVENT}, got {len(codes)}")
        for code in codes:
            code_id = str(code.get("code_id", ""))
            if not code_id or not code.get("name") or not code.get("definition"):
                raise ValueError("code requires code_id, name, definition")
            code["tenant_id"] = str(tenant_id)
            self.codes[code_id] = dict(code)

    @event("DefinitionRefined")
    def refine_definition(
        self, tenant_id: UUID, code_id: UUID, definition: str, inclusion: str, exclusion: str
    ) -> None:
        """Refine one code's definition in THIS version."""
        code = self._code(str(code_id))
        code.update(
            {
                "tenant_id": str(tenant_id),
                "definition": definition,
                "inclusion": inclusion,
                "exclusion": exclusion,
            }
        )

    @event("PrototypeUpdated")
    def update_prototypes(
        self, tenant_id: UUID, code_id: UUID, exemplar_text_sha256: list[str]
    ) -> None:
        """Point one code at its gold exemplar texts (vector rebuild is derived)."""
        code = self._code(str(code_id))
        code["tenant_id"] = str(tenant_id)
        code["exemplar_text_sha256"] = list(exemplar_text_sha256)

    @event("ThresholdRecalibrated")
    def recalibrate_thresholds(
        self,
        tenant_id: UUID,
        code_id: UUID,
        tau_a: float,
        tau_qa: float,
        delta: float,
        n_gold: int,
        cv_range: dict[str, object],
    ) -> None:
        """Store fitted per-code thresholds (G2 output) on THIS version."""
        code = self._code(str(code_id))
        code.update(
            {
                "tenant_id": str(tenant_id),
                "tau_a": tau_a,
                "tau_qa": tau_qa,
                "delta": delta,
                "n_gold": n_gold,
                "cv_range": cv_range,
                "threshold_unreliable": n_gold < 20,
            }
        )

    @event("KeywordSetUpdated")
    def update_keywords(
        self, tenant_id: UUID, code_id: UUID, keywords: list[dict[str, object]]
    ) -> None:
        """Set one code's inclusion/exclusion keyword list (todo-17 synthesizes)."""
        if len(keywords) > MAX_KEYWORDS_PER_EVENT:
            raise ValueError(f"keyword batch > {MAX_KEYWORDS_PER_EVENT}")
        code = self._code(str(code_id))
        code["tenant_id"] = str(tenant_id)
        code["keywords"] = [dict(k) for k in keywords]

    def _code(self, code_id: str) -> dict[str, object]:
        code = self.codes.get(code_id)
        if code is None:
            raise ValueError(f"code {code_id} not in version {self.label}")
        return code

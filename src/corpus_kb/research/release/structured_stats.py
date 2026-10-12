"""U43 structured-output evidence for the release packet.

Per-model first-attempt-valid and repair-recovery rates, accumulated from
``coding.structured_output.StructuredOutcome`` results. Lives in the release
package because the release packet is its only consumer; the coding module
stays under the 250-line soft limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from corpus_kb.coding.structured_output import StructuredOutcome


@dataclass
class StructuredOutputStats:
    """Per-model first-attempt-valid + repair-recovery rates (release packet)."""

    attempts: dict[str, int] = field(default_factory=dict)
    first_attempt_valid: dict[str, int] = field(default_factory=dict)
    repair_recovered: dict[str, int] = field(default_factory=dict)
    invalid_output: dict[str, int] = field(default_factory=dict)

    def record(self, outcome: StructuredOutcome) -> None:
        model = outcome.model
        self.attempts[model] = self.attempts.get(model, 0) + 1
        if outcome.first_attempt_valid:
            self.first_attempt_valid[model] = self.first_attempt_valid.get(model, 0) + 1
        if outcome.repair_recovered:
            self.repair_recovered[model] = self.repair_recovered.get(model, 0) + 1
        if outcome.status == "invalid_output":
            self.invalid_output[model] = self.invalid_output.get(model, 0) + 1

    def to_report(self) -> dict[str, dict[str, float | int]]:
        """{model: {attempts, first_attempt_valid_rate, repair_recovery_rate,
        invalid_output}}; rates are 0.0 with zero attempts (never invented)."""
        report: dict[str, dict[str, float | int]] = {}
        for model in sorted(self.attempts):
            total = self.attempts[model]
            report[model] = {
                "attempts": total,
                "first_attempt_valid_rate": (
                    self.first_attempt_valid.get(model, 0) / total if total else 0.0
                ),
                "repair_recovery_rate": (
                    self.repair_recovered.get(model, 0) / total if total else 0.0
                ),
                "invalid_output": self.invalid_output.get(model, 0),
            }
        return report

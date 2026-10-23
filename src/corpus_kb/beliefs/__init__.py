"""Deep Bayesian beliefs (Living Corpus W4).

``beliefs.math_core`` — exact conjugate Beta posteriors: power-prior
forgetting, empirical-Bayes pooling, equal-tail credible intervals.
``beliefs.eras`` — Beta-Bernoulli BOCPD segmentation (the history view).
``beliefs.rebuild`` — statements + conflict flags -> per-belief evidence.
``beliefs.store`` — rebuild-write and the read paths.
"""

from __future__ import annotations

from .eras import Era, eras
from .math_core import Evidence, Posterior, posterior, tenant_prior
from .rebuild import BeliefEvidence, BeliefKey, build_evidence, stance_of

__all__ = [
    "BeliefEvidence",
    "BeliefKey",
    "Era",
    "Evidence",
    "Posterior",
    "build_evidence",
    "eras",
    "posterior",
    "stance_of",
    "tenant_prior",
]

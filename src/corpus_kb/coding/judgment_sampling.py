"""U35 judgment-distribution sampling: the production mean-over-samples path.

``coding/uncertainty/judgment_distribution.py`` is the pure mean/mode
computation; THIS module is the coding-loop side — it turns
``research.judgment_distribution.use_mean`` into extra temperature draws of
the same coder window. Split from coder_dispatch.py (250-line soft limit):
the sampling mechanics live here, the dispatch call site keeps only the
wiring (route on the mean, greedy stays the decision source).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from corpus_kb.coding.coder_client import call_ollama
from corpus_kb.coding.coder_window import parse_decisions
from corpus_kb.coding.uncertainty.judgment_distribution import (
    JudgmentMeanResult,
    judgment_mean,
)
from corpus_kb.config import load_config

__all__ = [
    "JUDGMENT_MEAN_SAMPLES",
    "JUDGMENT_SAMPLE_TEMPERATURE",
    "judgment_mean_enabled",
    "sample_judgments",
]


# U35 judgment-distribution sampling (research.judgment_distribution.use_mean):
# when the key is on, dispatch_chunk makes this many EXTRA temperature draws of
# the same window (at JUDGMENT_SAMPLE_TEMPERATURE) and routes on the mean
# per-code confidence instead of the single greedy value. The greedy reply
# stays the decision source (decision/quote/rationale are never sampled); only
# the routed confidence becomes the distribution mean. Off (default): zero
# extra calls — output is byte-identical to the single-greedy-call path.
JUDGMENT_MEAN_SAMPLES = 4
JUDGMENT_SAMPLE_TEMPERATURE = 0.7


def judgment_mean_enabled(config: dict[str, object] | None = None) -> bool:
    """The ``research.judgment_distribution.use_mean`` flag (U35), typed-read.

    Uses the canonical measurement reader (coding/measurement_settings.py,
    the same defaults that populate both config.yaml files and the doctor's
    U35 check). ``load_config()`` always carries the measurement blocks (its
    defaults are merged from MEASUREMENT_CONFIG_DEFAULTS), so the zero-arg
    call resolves cleanly; a caller passing a bare mapping without the
    blocks gets the typed reader's defaults, and a NON-MAPPING research
    block (or judgment_distribution block) raises loudly rather than
    silently sampling — or not sampling — on a broken config.
    """
    from corpus_kb.coding.measurement_settings import measurement_settings_from_mapping

    research = (config if config is not None else load_config()).get("research", {})
    if not isinstance(research, dict):
        raise ValueError(
            "config.research must be a mapping to read research.judgment_distribution.use_mean"
        )
    return measurement_settings_from_mapping(research).judgment_distribution.use_mean


async def sample_judgments(
    window: str,
    code_ids: list[str],
    greedy_reply: dict[str, Any],
    call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None,
    model: str,
) -> dict[str, JudgmentMeanResult]:
    """Temperature resamples of the window -> judgment_mean per code (U35).

    The greedy reply is kept as the first sample (it IS one draw, at
    temperature 0); each extra draw that fails to parse is skipped rather
    than failing the dispatch — the decision source above already parsed.
    A code with only the greedy sample therefore gets mean == mode == its
    greedy confidence, reported honestly via n_samples.
    """
    replies: list[dict[str, Any]] = [greedy_reply]
    for _ in range(JUDGMENT_MEAN_SAMPLES):
        replies.append(
            await (
                call_fn(window)
                if call_fn is not None
                else call_ollama(window, model, temperature=JUDGMENT_SAMPLE_TEMPERATURE)
            )
        )
    samples: dict[str, list[float]] = {code_id: [] for code_id in code_ids}
    for reply in replies:
        parsed, _error = parse_decisions(reply, code_ids)
        if not parsed:
            continue
        for code_id, output in parsed.items():
            samples[code_id].append(output.confidence)
    return {code_id: judgment_mean(values, enabled=True) for code_id, values in samples.items()}

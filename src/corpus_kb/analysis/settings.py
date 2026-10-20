"""Typed ``analysis.*`` settings (the corpus analysis program, Wave 0).

Same house pattern as ``topics/settings.py`` (Wave 4): the module owns
the defaults, frozen dataclasses carry the typed reads, and a loader
validates so the YAML files, ``config.py`` defaults and this module
cannot drift. The defaults merge into every ``config.load_config()``
result through ``config._apply_typed_module_defaults``
(ANALYSIS_CONFIG_DEFAULTS). Wave 0 ships the keys with every feature
OFF; the engines land in later waves and the ops answer honest no-op
envelopes until then.

Keys and their contracts:

- ``analysis.labeling.enabled``: the weak-supervision feature flag.
  ``labeling_build``/``labeling_list``/``labeling_lfs`` refuse honestly
  while it is false, never silently.
- ``analysis.labeling.min_signals``: how many LFs must fire before the
  aggregator accepts a label (the ``aggregate_signals`` min_signals).
  Must be a positive int (booleans are rejected: bool is an int
  subclass). The default is 3: one correlated signal is not evidence
  (the weak-supervision conflict rule).
- ``analysis.labeling.lfs_file``: path to the labeling-function
  definitions (JSON). ``None`` means no LFs are registered, so a build
  answers an honest empty envelope, never a fake run.
- ``analysis.classification.enabled``: the few-shot classification flag.
- ``analysis.classification.tier``: only ``head`` (logistic head on the
  stored embeddings) and ``setfit`` (the opt-in ``[setfit]`` extra)
  exist. Any other value is rejected AT LOAD (``config.load_config``
  raises with the remediation text), never silently ignored at build
  time.
- ``analysis.classification.min_labels_per_class``: the seed floor. The
  SetFit paper's claims start at 8 labels per class; below it the build
  must answer an honest insufficient-labels envelope, not a guess.
- ``analysis.topics.over_time.enabled``: the temporal topic-trends flag
  (``topics_trends``).
- ``analysis.topics.over_time.bins``: ``"auto"`` or a positive int (the
  ``topics.min_topic_size`` union rule). ``"auto"`` resolves here and
  the resolved value is reported (the honest-defaults convention).
- ``analysis.topics.over_time.undated``: only ``drop`` and ``report``
  exist. ``drop`` (the default policy) skips undated documents and the
  envelope reports the count; ``report`` lists them instead. There is no
  ``NaN`` date in the trends build, so ``ignore`` is deliberately
  absent.
- ``analysis.topics.max_embeddings``: the fit-sample cap for a topic
  build. ``None`` means the engine default applies (20000). The engine
  fits the sample and assigns the rest by transform, never by a second
  fit.

Determinism pins stay CODE CONSTANTS in the engines that follow (the
``research/inductive`` convention): only governance knobs live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

DEFAULT_LABELING_ENABLED = False
DEFAULT_LABELING_MIN_SIGNALS = 3
DEFAULT_LFS_FILE: str | None = None
DEFAULT_CLASSIFICATION_ENABLED = False
DEFAULT_CLASSIFICATION_TIER = "head"
DEFAULT_MIN_LABELS_PER_CLASS = 8
DEFAULT_OVER_TIME_ENABLED = False
DEFAULT_OVER_TIME_BINS: str | int = "auto"
DEFAULT_OVER_TIME_UNDATED = "drop"
DEFAULT_TOPICS_MAX_EMBEDDINGS: int | None = None

SUPPORTED_CLASSIFICATION_TIERS = frozenset({"head", "setfit"})
SUPPORTED_UNDATED_POLICIES = frozenset({"drop", "report"})

# What "auto" resolves to; the resolved value is reported per build (the
# honest-defaults convention, mirroring topics AUTO_MIN_TOPIC_SIZE).
AUTO_OVER_TIME_BINS = 10

LABELING_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_LABELING_ENABLED,
    "min_signals": DEFAULT_LABELING_MIN_SIGNALS,
    "lfs_file": DEFAULT_LFS_FILE,
}

CLASSIFICATION_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_CLASSIFICATION_ENABLED,
    "tier": DEFAULT_CLASSIFICATION_TIER,
    "min_labels_per_class": DEFAULT_MIN_LABELS_PER_CLASS,
}

OVER_TIME_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_OVER_TIME_ENABLED,
    "bins": DEFAULT_OVER_TIME_BINS,
    "undated": DEFAULT_OVER_TIME_UNDATED,
}

ANALYSIS_CONFIG_DEFAULTS: dict[str, object] = {
    "labeling": dict(LABELING_CONFIG_DEFAULTS),
    "classification": dict(CLASSIFICATION_CONFIG_DEFAULTS),
    "topics": {
        "over_time": dict(OVER_TIME_CONFIG_DEFAULTS),
        "max_embeddings": DEFAULT_TOPICS_MAX_EMBEDDINGS,
    },
}


@dataclass(frozen=True)
class LabelingSettings:
    """Typed ``analysis.labeling.*`` reads (WP-2)."""

    enabled: bool = DEFAULT_LABELING_ENABLED
    min_signals: int = DEFAULT_LABELING_MIN_SIGNALS
    lfs_file: str | None = DEFAULT_LFS_FILE


@dataclass(frozen=True)
class ClassificationSettings:
    """Typed ``analysis.classification.*`` reads (WP-3)."""

    enabled: bool = DEFAULT_CLASSIFICATION_ENABLED
    tier: str = DEFAULT_CLASSIFICATION_TIER
    min_labels_per_class: int = DEFAULT_MIN_LABELS_PER_CLASS


@dataclass(frozen=True)
class OverTimeSettings:
    """Typed ``analysis.topics.over_time.*`` reads (WP-4)."""

    enabled: bool = DEFAULT_OVER_TIME_ENABLED
    bins: str | int = DEFAULT_OVER_TIME_BINS
    undated: str = DEFAULT_OVER_TIME_UNDATED

    def resolved_bins(self) -> int:
        """The int the trends build actually receives ("auto" resolves here)."""
        if isinstance(self.bins, str):
            return AUTO_OVER_TIME_BINS
        return int(self.bins)


@dataclass(frozen=True)
class AnalysisSettings:
    """Typed ``analysis.*`` reads (the analysis program)."""

    labeling: LabelingSettings = field(default_factory=LabelingSettings)
    classification: ClassificationSettings = field(default_factory=ClassificationSettings)
    over_time: OverTimeSettings = field(default_factory=OverTimeSettings)
    topics_max_embeddings: int | None = DEFAULT_TOPICS_MAX_EMBEDDINGS


def _positive_int_error(dotted: str, value: object) -> str | None:
    """A positive int (bools are ints — reject them)."""
    if isinstance(value, bool):
        return f"{dotted}: must be a positive int (booleans are not counts); got {value!r}"
    if isinstance(value, int) and value > 0:
        return None
    return f"{dotted}: must be a positive int; got {value!r}"


def _bins_error(value: object) -> str | None:
    """``"auto"`` or a positive int (the min_topic_size union rule)."""
    if isinstance(value, bool):
        return (
            "analysis.topics.over_time.bins: must be the string 'auto' or a positive "
            f"int (booleans are not counts); got {value!r}"
        )
    if isinstance(value, str):
        if value == "auto":
            return None
        return (
            "analysis.topics.over_time.bins: must be the string 'auto' or a positive "
            f"int; got {value!r}"
        )
    if isinstance(value, int) and value > 0:
        return None
    return (
        "analysis.topics.over_time.bins: must be the string 'auto' or a positive int "
        f"(the number of date bins in the trends build); got {value!r}"
    )


def analysis_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid analysis values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so
    an unknown tier or a bad bins value fails AT LOAD, never mid-build.
    """
    labeling = _labeling_block(config)
    min_signals_error = _positive_int_error(
        "analysis.labeling.min_signals",
        labeling.get("min_signals", DEFAULT_LABELING_MIN_SIGNALS),
    )
    if min_signals_error is not None:
        return min_signals_error
    lfs_file = labeling.get("lfs_file", DEFAULT_LFS_FILE)
    if lfs_file is not None and not isinstance(lfs_file, str):
        return f"analysis.labeling.lfs_file: must be a path string or null; got {lfs_file!r}"

    classification = _classification_block(config)
    tier = classification.get("tier", DEFAULT_CLASSIFICATION_TIER)
    if tier not in SUPPORTED_CLASSIFICATION_TIERS:
        tiers = ", ".join(sorted(SUPPORTED_CLASSIFICATION_TIERS))
        return (
            f"analysis.classification.tier: {tier!r} is not supported. Only {tiers} is "
            "available. Set tier: head or tier: setfit, or remove the key."
        )
    floor_error = _positive_int_error(
        "analysis.classification.min_labels_per_class",
        classification.get("min_labels_per_class", DEFAULT_MIN_LABELS_PER_CLASS),
    )
    if floor_error is not None:
        return floor_error

    over_time = _over_time_block(config)
    bins_error = _bins_error(over_time.get("bins", DEFAULT_OVER_TIME_BINS))
    if bins_error is not None:
        return bins_error
    undated = over_time.get("undated", DEFAULT_OVER_TIME_UNDATED)
    if undated not in SUPPORTED_UNDATED_POLICIES:
        policies = ", ".join(sorted(SUPPORTED_UNDATED_POLICIES))
        return (
            f"analysis.topics.over_time.undated: {undated!r} is not supported. Only "
            f"{policies} is available. Set undated: drop or undated: report, or remove "
            "the key."
        )
    topics_block = _child_block(_analysis_block(config), "topics")
    max_embeddings = topics_block.get("max_embeddings", DEFAULT_TOPICS_MAX_EMBEDDINGS)
    if max_embeddings is not None:
        max_error = _positive_int_error("analysis.topics.max_embeddings", max_embeddings)
        if max_error is not None:
            return max_error
    return None


def load_analysis_settings(config: dict[str, object]) -> AnalysisSettings:
    """Read ``analysis.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config uses,
    so direct readers (tests, tools) fail as loudly as a config load.
    """
    error = analysis_settings_error(config)
    if error is not None:
        raise ValueError(error)
    labeling = _labeling_block(config)
    classification = _classification_block(config)
    over_time = _over_time_block(config)
    bins = over_time.get("bins", DEFAULT_OVER_TIME_BINS)
    typed_bins: str | int
    if isinstance(bins, bool):
        # Unreachable behind analysis_settings_error, but keep the type honest.
        raise ValueError(_bins_error(bins) or "invalid analysis.topics.over_time.bins")
    typed_bins = bins if isinstance(bins, int) else str(bins)
    return AnalysisSettings(
        labeling=LabelingSettings(
            enabled=bool(labeling.get("enabled", DEFAULT_LABELING_ENABLED)),
            min_signals=int(labeling.get("min_signals", DEFAULT_LABELING_MIN_SIGNALS)),
            lfs_file=cast("str | None", labeling.get("lfs_file", DEFAULT_LFS_FILE)),
        ),
        classification=ClassificationSettings(
            enabled=bool(classification.get("enabled", DEFAULT_CLASSIFICATION_ENABLED)),
            tier=str(classification.get("tier", DEFAULT_CLASSIFICATION_TIER)),
            min_labels_per_class=int(
                classification.get("min_labels_per_class", DEFAULT_MIN_LABELS_PER_CLASS)
            ),
        ),
        over_time=OverTimeSettings(
            enabled=bool(over_time.get("enabled", DEFAULT_OVER_TIME_ENABLED)),
            bins=typed_bins,
            undated=str(over_time.get("undated", DEFAULT_OVER_TIME_UNDATED)),
        ),
        topics_max_embeddings=cast(
            "int | None",
            _child_block(_analysis_block(config), "topics").get(
                "max_embeddings", DEFAULT_TOPICS_MAX_EMBEDDINGS
            ),
        ),
    )


def _child_block(parent: dict[str, object], key: str) -> dict[str, object]:
    """The named child mapping, or an empty one."""
    block = parent.get(key)
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, object], block)


def _analysis_block(config: dict[str, object]) -> dict[str, object]:
    """The ``analysis`` mapping, or an empty one."""
    return _child_block(config, "analysis")


def _labeling_block(config: dict[str, object]) -> dict[str, object]:
    """The ``analysis.labeling`` mapping, or an empty one."""
    return _child_block(_analysis_block(config), "labeling")


def _classification_block(config: dict[str, object]) -> dict[str, object]:
    """The ``analysis.classification`` mapping, or an empty one."""
    return _child_block(_analysis_block(config), "classification")


def _over_time_block(config: dict[str, object]) -> dict[str, object]:
    """The ``analysis.topics.over_time`` mapping, or an empty one."""
    topics = _child_block(_analysis_block(config), "topics")
    return _child_block(topics, "over_time")

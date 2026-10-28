"""The ``calibrate:`` block of a profile: its spec and its parser.

The block sweeps query-time recipes over labeled golden queries.
``sample`` and ``golden`` resolve against the profiles directory, so a
profile stays portable with its corpus. ``candidates`` holds names or
single-key mappings (``tight: {retrieval.rrf_k: 10}``); a mapping names
one candidate and its query-time recipe overrides. ``baseline`` names the
family member every challenger must beat (default: the first candidate).
Unknown keys, unknown recipe keys, and more than eight candidates fail
loudly — the house pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError

KNOWN_CALIBRATE_KEYS: frozenset[str] = frozenset(
    {"candidates", "sample", "golden", "k", "min_overlap", "baseline", "alpha", "min_queries"}
)


@dataclass(frozen=True)
class CalibrateSpec:
    """The calibrate-then-lock sweep block (recipe PR-1).

    ``candidates`` lists family member names, in authored order.
    ``recipes`` maps a candidate name to its query-time overrides; names
    absent from ``recipes`` rank with the untouched config. ``baseline``
    names the reference member (default: the first candidate).
    """

    candidates: tuple[str, ...]
    sample: str
    golden: str
    k: int = 10
    min_overlap: float = 0.5
    baseline: str | None = None
    alpha: float = 0.05
    min_queries: int = 30
    recipes: dict[str, dict[str, object]] = field(default_factory=dict)


def parse_calibrate(raw: Any, name: str) -> CalibrateSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"Profile {name!r} calibrate must be a mapping.",
            remediation=(
                "Use candidates, sample, golden, and optional k / min_overlap /"
                " baseline / alpha / min_queries."
            ),
        )
    _reject_unknown_calibrate_keys(raw, KNOWN_CALIBRATE_KEYS, name, "calibrate")
    candidates, recipes = _calibrate_candidates(raw.get("candidates"), name)
    baseline = raw.get("baseline")
    if baseline is not None and (not isinstance(baseline, str) or not baseline):
        raise ValidationError(
            f"Profile {name!r} calibrate.baseline must be a candidate name.",
            remediation="Name one member of calibrate.candidates, e.g. default.",
        )
    if baseline is not None and baseline not in candidates:
        raise ValidationError(
            f"Profile {name!r} calibrate.baseline {baseline!r} is not a candidate.",
            remediation="Add it to calibrate.candidates, or name an existing member.",
        )
    return CalibrateSpec(
        candidates=candidates,
        sample=_calibrate_path(raw.get("sample"), "sample", name, "a file"),
        golden=_calibrate_path(raw.get("golden"), "golden", name, "a JSONL file"),
        k=_calibrate_k(raw.get("k", 10), name),
        min_overlap=_calibrate_min_overlap(raw.get("min_overlap", 0.5), name),
        baseline=baseline,
        alpha=_calibrate_alpha(raw.get("alpha", 0.05), name),
        min_queries=_calibrate_min_queries(raw.get("min_queries", 30), name),
        recipes=recipes,
    )


def _reject_unknown_calibrate_keys(
    raw: dict[str, Any], known: frozenset[str], name: str, block: str
) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValidationError(
            f"Profile {name!r} {block} has unknown keys: {', '.join(unknown)}.",
            remediation=f"Allowed keys: {', '.join(sorted(known))}.",
        )


def _calibrate_candidates(
    raw: Any, name: str
) -> tuple[tuple[str, ...], dict[str, dict[str, object]]]:
    from corpus_kb.eval.recipes import MAX_CANDIDATES, validate_candidates

    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates must be a non-empty list.",
            remediation="Name at least one candidate, e.g. [default] or"
            " [default, tight: {retrieval.rrf_k: 10}].",
        )
    names: list[str] = []
    recipes: dict[str, dict[str, object]] = {}
    for item in raw:
        if isinstance(item, str) and item:
            names.append(item)
            continue
        if isinstance(item, dict) and len(item) == 1:
            candidate, overrides = next(iter(item.items()))
            if not isinstance(candidate, str) or not candidate or not isinstance(overrides, dict):
                raise ValidationError(
                    f"Profile {name!r} calibrate.candidates entries must be names or"
                    " one-candidate recipe mappings.",
                    remediation="Write either a name or 'name: {key: value}' with query-time keys.",
                )
            names.append(candidate)
            recipes[candidate] = dict(overrides)
            continue
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates entries must be names or recipe"
            " mappings holding one candidate each.",
            remediation="List each candidate on its own line as 'name: {overrides}'.",
        )
    duplicates = sorted({c for c in names if names.count(c) > 1})
    if duplicates:
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates repeats: {', '.join(duplicates)}.",
            remediation="List each candidate once.",
        )
    if len(names) > MAX_CANDIDATES:
        raise ValidationError(
            f"Profile {name!r} calibrate.candidates allows at most {MAX_CANDIDATES}"
            f" members; got {len(names)}.",
            remediation=f"Trim the list to {MAX_CANDIDATES} or fewer candidates.",
        )
    validate_candidates(recipes)
    return tuple(names), recipes


def _calibrate_path(raw: Any, key: str, name: str, kind: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"Profile {name!r} calibrate.{key} must be a path.",
            remediation=f"Point calibrate.{key} at {kind} in the profiles directory.",
        )
    return raw


def _calibrate_k(raw: Any, name: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ValidationError(
            f"Profile {name!r} calibrate.k must be a positive integer.",
            remediation="Set calibrate.k to a positive integer, e.g. 10.",
        )
    return raw


def _calibrate_min_overlap(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0.0 < float(raw) <= 1.0:
        raise ValidationError(
            f"Profile {name!r} calibrate.min_overlap must fall in (0, 1].",
            remediation="Set calibrate.min_overlap to a fraction, e.g. 0.5.",
        )
    return float(raw)


def _calibrate_alpha(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0.0 < float(raw) < 1.0:
        raise ValidationError(
            f"Profile {name!r} calibrate.alpha must fall in (0, 1).",
            remediation="Set calibrate.alpha to a significance level, e.g. 0.05.",
        )
    return float(raw)


def _calibrate_min_queries(raw: Any, name: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ValidationError(
            f"Profile {name!r} calibrate.min_queries must be a non-negative integer.",
            remediation="Set calibrate.min_queries to a query count, e.g. 30.",
        )
    return raw

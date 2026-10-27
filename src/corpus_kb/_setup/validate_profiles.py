"""Profile validation gate (Living Corpus W5).

``poe validate-profiles`` / the CI validate-profiles job run this. It
scans the profiles directory and fails loudly when any profile file is
broken: schema violations (via ``profiles.loader.parse_profile``),
unknown ingestion-strategy names, unknown claim-template ids, and a
declared seeds file that does not exist. Cross-references are checked
against the live strategy registry and the claim-template catalogue,
so a profile can never reference a seam that is not there.

Exit 0 with a one-line-per-profile report when clean; exit 1 with the
error list otherwise. A missing profiles directory is clean (a project
without profiles is valid; the shipped starter lives in the repo).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "config" / "claim_templates"


def validate_profiles(
    profiles_dir: Path = DEFAULT_PROFILES_DIR,
    templates_dir: Path = DEFAULT_TEMPLATES_DIR,
) -> list[str]:
    """Validate every ``*.yaml`` profile; returns the error lines.

    Seeds files (``*.seeds.yaml``, the extraction-tune output) are NOT
    profiles and are skipped. A missing claim-template catalogue is a
    gate error: template cross-references must stay checkable.
    """
    errors: list[str] = []
    if not profiles_dir.is_dir():
        return errors
    if not templates_dir.is_dir():
        errors.append(
            f"claim template catalogue not found at {templates_dir};"
            " template cross-references cannot be checked."
        )
    template_ids = _template_ids(templates_dir, errors)
    for path in sorted(profiles_dir.glob("*.yaml")):
        if path.name.endswith(".seeds.yaml"):
            continue
        errors.extend(_validate_one(path, profiles_dir, template_ids))
    return errors


def _template_ids(templates_dir: Path, errors: list[str]) -> set[str]:
    """Every claim-template id in the catalogue (empty on read failure)."""
    from corpus_kb.statements.templates import load_templates

    if not templates_dir.is_dir():
        return set()
    try:
        catalogue = load_templates(templates_dir)
    except Exception as exc:
        errors.append(f"claim template catalogue unreadable: {exc}")
        return set()
    return set(catalogue.ids())


def _validate_one(path: Path, profiles_dir: Path, template_ids: set[str]) -> list[str]:
    from corpus_kb.errors import CorpusError
    from corpus_kb.profiles.loader import parse_profile

    name = path.stem
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile = parse_profile(raw, name, str(path))
    except CorpusError as exc:
        return [f"{path.name}: {exc}"]
    except Exception as exc:
        return [f"{path.name}: unreadable YAML: {exc}"]
    errors: list[str] = []
    errors.extend(_check_strategies(path.name, profile))
    errors.extend(_check_calibrate(path.name, profile, profiles_dir))
    for template_id in profile.templates:
        if template_ids and template_id not in template_ids:
            errors.append(
                f"{path.name}: template id {template_id!r} is not in the claim-template catalogue."
            )
    if profile.seeds_file is not None:
        seeds = profiles_dir / profile.seeds_file
        if not seeds.exists():
            errors.append(
                f"{path.name}: seeds file {profile.seeds_file!r} not found under {profiles_dir}."
            )
    errors.extend(_check_lock(path, profiles_dir))
    return errors


def _check_calibrate(name: str, profile: object, profiles_dir: Path) -> list[str]:
    """The calibrate block's candidates, sample, and golden must all exist."""
    from corpus_kb.ingestion.registry import UnknownStrategyError, get_strategy

    spec = getattr(profile, "calibrate", None)
    if spec is None:
        return []
    errors: list[str] = []
    for candidate in spec.candidates:
        try:
            get_strategy(candidate)
        except UnknownStrategyError:
            errors.append(f"{name}: calibrate.candidates names unknown strategy {candidate!r}.")
    golden = profiles_dir / spec.golden
    if not golden.is_file():
        errors.append(f"{name}: calibrate.golden {spec.golden!r} not found under {profiles_dir}.")
    sample = profiles_dir / spec.sample
    if not sample.exists():
        errors.append(f"{name}: calibrate.sample {spec.sample!r} not found under {profiles_dir}.")
    return errors


def _check_lock(path: Path, profiles_dir: Path) -> list[str]:
    """A stale calibration lock fails the gate (Wave 7: enforce parity)."""
    from corpus_kb.errors import ValidationError
    from corpus_kb.eval.lockfile import check_profile_lock

    lock = profiles_dir / f"{path.stem}.lock.json"
    if not lock.is_file():
        return []
    try:
        check_profile_lock(path, lock)
    except ValidationError as exc:
        return [f"{path.name}: {exc}"]
    return []


def _check_strategies(name: str, profile: object) -> list[str]:
    """Every named strategy must resolve from the live registry."""
    from corpus_kb.ingestion.registry import UnknownStrategyError, get_strategy

    errors: list[str] = []
    strategies: dict[str, str] = getattr(profile, "strategies", {}) or {}
    default = getattr(profile, "default_strategy", None)
    for source_type, strategy_name in {**strategies, "default": default}.items():
        if not strategy_name:
            continue
        try:
            get_strategy(strategy_name)
        except UnknownStrategyError:
            errors.append(
                f"{name}: unknown ingestion strategy {strategy_name!r} "
                f"(mapped for {source_type!r})."
            )
    return errors


def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROFILES_DIR
    templates = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TEMPLATES_DIR
    errors = validate_profiles(directory, templates)
    if errors:
        for line in errors:
            print(f"ERROR: {line}")
        print(f"validate-profiles: {len(errors)} problem(s) found.")
        return 1
    profiles = sorted(p.name for p in directory.glob("*.yaml")) if directory.is_dir() else []
    print(f"validate-profiles: {len(profiles)} profile(s) checked, all clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

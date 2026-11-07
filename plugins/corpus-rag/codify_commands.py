"""The codify command group: chunk coding over the coding ops (issue #354).

Every command picks its study like the rest of the plugin (4-step
selection), reaps idle servers, and turns the study server up, then
sends ONE coding op through the op runner. ``codify cells`` builds the
next cells file from read-only SQL instead (see ``codify_cells``).

The paradigm gate: ``codify provision`` records the study's paradigm,
and ``reliability`` refuses for any study whose paradigm is not
``codebook`` BEFORE it touches a server. Reflexive thematic analysis
rejects inter-coder reliability: coders are expected to differ, so an
alpha or kappa would hide that, not prove quality.

Stdlib only.
"""

from __future__ import annotations

import csv
import json
import sys

import codify_cells
import codify_common
import study_admin
import study_commands
import study_registry

PARADIGMS = ("codebook", "reflexive")
BAND_KEYS = ("high", "mid", "low")

REFLEXIVE_REFUSAL = (
    "not ok: study '{slug}' uses the reflexive paradigm. Reflexive "
    "thematic analysis treats codes as something the coder and the data "
    "make together. Coders are expected to differ, so inter-coder "
    "reliability numbers (alpha, kappa) would hide that, not prove "
    "quality. This command runs only for codebook (deductive) studies."
)
NO_PARADIGM_REFUSAL = (
    "not ok: study '{slug}' has no paradigm on record. If this study "
    "follows codebook (deductive) coding, record it:\n"
    "  corpus-rag codify provision {slug} --paradigm codebook\n"
    "Reflexive thematic analysis does not use inter-coder reliability."
)
CAUTION = (
    "**Caution:** alpha and kappa measure how much coders agree. They do "
    "not say whether a code is true, whether the quotes are honest, or "
    "whether the codebook fits the corpus. A high number can hide a "
    "shared mistake; a low number can mean the material is rich. Treat "
    "the number as one input; the judgment stays with you."
)


def _validated_bands(raw: str) -> str:
    """``--bands`` must be a JSON object keyed only high, mid, low (0 to 1)."""
    try:
        bands = json.loads(raw)
    except ValueError as exc:
        raise codify_common.CodifyError(
            1,
            '--bands must be a JSON object with the keys high, mid, low, '
            'like {"high":0.9,"mid":0.7,"low":0.5}.',
        ) from exc
    if not isinstance(bands, dict) or not bands:
        raise codify_common.CodifyError(
            1, "--bands accepts only the keys high, mid, low, as a JSON object."
        )
    unknown = sorted(set(bands) - set(BAND_KEYS))
    if unknown:
        raise codify_common.CodifyError(
            1,
            "--bands accepts only the keys high, mid, low. Unknown: "
            + ", ".join(unknown)
            + ".",
        )
    for key in sorted(bands):
        value = bands[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise codify_common.CodifyError(
                1,
                "--bands accepts only the keys high, mid, low, and each value "
                f"must be a number between 0 and 1. {key} is not.",
            )
        if not 0 <= float(value) <= 1:
            raise codify_common.CodifyError(
                1,
                "--bands accepts only the keys high, mid, low, and each value "
                f"must be a number between 0 and 1. {key} is out of range.",
            )
    return json.dumps(bands)


def cmd_codify_provision(args) -> int:
    """Provision the study (when new), then record its paradigm."""
    if args.paradigm not in PARADIGMS:
        print("not ok: --paradigm must be codebook or reflexive.")
        return 2
    registry = study_registry.load_registry()
    slug = study_registry.slugify(args.slug)
    if slug not in registry.get("studies", {}):
        code = study_admin._provision_study(args)
        if code != 0:
            return code
        registry = study_registry.load_registry()
    else:
        print(f"A study named '{slug}' already exists; recording the paradigm.")
    registry["studies"][slug]["paradigm"] = args.paradigm
    study_registry.save_registry(registry)
    print(f"ok: study '{slug}' records the {args.paradigm} paradigm.")
    if args.paradigm == "reflexive":
        print("`corpus-rag codify reliability` will refuse for this study.")
    print(f"Next: corpus-rag ingest <path> -p {slug}")
    return 0


def cmd_codify_load_codebook(args) -> int:
    """Validate, embed, and load one codebook; print its version id."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    text = codify_common.read_json_text(args.file)
    codify_common.parse_json(text, args.file)
    envelope = codify_common.send(cli, "coding_embed_codebook", [], study, json_blob=text)
    if envelope.get("status") != "success":
        print(f"not ok: the engine refused the codebook for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(f"ok: codebook loaded into '{slug}'. Version id: {envelope.get('codebook_version_id')}")
    print("Keep this version id. `corpus-rag codify pool` uses the latest version by default.")
    return 0


def cmd_codify_pool(args) -> int:
    """Pool keyword and vector candidates for one codebook version."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    version = args.codebook_version or codify_cells.latest_codebook_version(study)
    envelope = codify_common.send(cli, "coding_pool", [f"codebook_version_id={version}"], study)
    if envelope.get("status") != "success":
        print(f"not ok: pooling failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(f"ok: pooled for codebook version {version} in '{slug}'.")
    for key in ("keyword_governance", "speaker_roles", "coverage"):
        if key in envelope:
            print(f"{key}:")
            print(json.dumps(envelope.get(key), indent=2, default=str))
    return 0


def cmd_codify_code(args) -> int:
    """Code one batch of cells with confidence routing."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    cells = codify_common.parse_json_list(args.cells)
    pairs = [f"cells={json.dumps(cells)}"]
    if args.coder:
        pairs.append(f"coder={args.coder}")
    if args.model:
        pairs.append(f"model={args.model}")
    if args.batch_id:
        pairs.append(f"batch_id={args.batch_id}")
    if args.coding_pass is not None:
        pairs.append(f"coding_pass={args.coding_pass}")
    if args.bands:
        pairs.append(f"bands={_validated_bands(args.bands)}")
    envelope = codify_common.send(cli, "coding_code_batch", pairs, study)
    if envelope.get("status") != "success":
        print(f"not ok: the coding batch failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(
        f"ok: coded '{slug}' batch: {envelope.get('cells_coded')} coded, "
        f"{envelope.get('cells_rejected')} rejected."
    )
    results = envelope.get("results")
    if isinstance(results, list) and results:
        print(json.dumps(results, indent=2, default=str)[:2000])
    return 0


def cmd_codify_reliability(args) -> int:
    """Alpha/kappa reliability; the paradigm gate runs before any server call."""
    registry = study_registry.load_registry()
    slug, _study = study_registry.resolve_study(registry, args.study_slug)
    paradigm = _study.get("paradigm")
    if paradigm != "codebook":
        if paradigm == "reflexive":
            print(REFLEXIVE_REFUSAL.format(slug=slug))
        else:
            print(NO_PARADIGM_REFUSAL.format(slug=slug))
        return 1
    cli, _slug, study = study_commands.scoped_study(args.study_slug)
    envelope = codify_common.send(
        cli,
        "coding_reliability",
        [f"per_code_min={args.per_code_min}", f"seed={args.seed}"],
        study,
    )
    if envelope.get("status") != "success":
        print(f"not ok: reliability failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(f"Reliability for '{slug}':")
    print(json.dumps(envelope.get("overall"), indent=2, default=str))
    per_code = envelope.get("per_code")
    if isinstance(per_code, list) and per_code:
        print("Per code:")
        print(json.dumps(per_code, indent=2, default=str)[:2000])
    if envelope.get("flags"):
        print(f"Flags: {json.dumps(envelope.get('flags'), default=str)}")
    print(CAUTION)
    return 0


def cmd_codify_calibrate_floors(args) -> int:
    """Calibrate one code's pool and residual floors from a labels file."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    text = codify_common.read_json_text(args.labels)
    labels = codify_common.parse_json(text, args.labels)
    if not isinstance(labels, dict):
        raise codify_common.CodifyError(1, f"{args.labels} must hold a JSON object.")
    envelope = codify_common.send(
        cli, "coding_calibrate_floors", [], study, json_blob=text
    )
    if envelope.get("status") != "success":
        print(f"not ok: floor calibration failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(
        f"ok: floors for {envelope.get('code_id')} in '{slug}': "
        f"pool {envelope.get('pool_floor')}, residual {envelope.get('residual_floor')}."
    )
    if envelope.get("fallback_applied"):
        print("A global fallback floor was applied; the labeled set was too small.")
    return 0


def cmd_codify_saturation(args) -> int:
    """The saturation signal and the batch stop rule."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    pairs = []
    if args.batch_id:
        pairs.append(f"batch_id={args.batch_id}")
    pairs.append(f"threshold={args.threshold}")
    pairs.append(f"min_samples={args.min_samples}")
    envelope = codify_common.send(cli, "coding_saturation", pairs, study)
    if envelope.get("status") != "success":
        print(f"not ok: saturation failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(f"Saturation for '{slug}':")
    for key in ("isr", "should_stop", "new_codes", "unique_codes", "total_applications"):
        if key in envelope:
            print(f"{key}: {envelope.get(key)}")
    if envelope.get("should_stop"):
        print("The stop rule says this batch can stop. Report saturation as provisional.")
    return 0


def cmd_codify_status(args) -> int:
    """Corpus counts, saturation ratio, coverage by disposition, routes."""
    cli, slug, study = study_commands.scoped_study(args.study_slug)
    envelope = codify_common.send(cli, "coding_status", [], study)
    if envelope.get("status") != "success":
        print(f"not ok: coding status failed for '{slug}'.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    print(f"Coding status for '{slug}':")
    for key in ("documents", "chunks", "saturation", "coverage", "routes"):
        if key in envelope:
            print(f"{key}: {json.dumps(envelope.get(key), indent=2, default=str)}")
    return 0


def cmd_codify_export(args) -> int:
    """Accepted codes joined with chunk text and document source."""
    cli, _slug, study = study_commands.scoped_study(args.study_slug)
    envelope = codify_common.send(cli, "coding_export", [], study)
    if envelope.get("status") != "success":
        print("not ok: the export failed.")
        print(json.dumps(envelope, indent=2, default=str)[:2000])
        return 1
    if args.format == "csv":
        columns = [str(column) for column in envelope.get("columns", [])]
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        for row in envelope.get("rows", []):
            writer.writerow([row.get(column) for column in columns])
        return 0
    print(json.dumps(envelope, indent=2, default=str))
    return 0


def cmd_codify_cells(args) -> int:
    """Build the next cells file: uncoded chunks with up to five candidates."""
    _cli, slug, study = study_commands.scoped_study(args.study_slug)
    return codify_cells.build_cells_file(
        slug, study, args.coder, args.limit, args.out
    )


CODIFY_HANDLERS = {
    "provision": cmd_codify_provision,
    "load-codebook": cmd_codify_load_codebook,
    "pool": cmd_codify_pool,
    "code": cmd_codify_code,
    "reliability": cmd_codify_reliability,
    "calibrate-floors": cmd_codify_calibrate_floors,
    "saturation": cmd_codify_saturation,
    "status": cmd_codify_status,
    "export": cmd_codify_export,
    "cells": cmd_codify_cells,
}


def cmd_codify(args) -> int:
    """The codify dispatcher; prints one honest line per failure."""
    try:
        return CODIFY_HANDLERS[args.codify_command](args)
    except codify_common.CodifyError as exc:
        print(f"not ok: {exc.message}")
        return exc.code

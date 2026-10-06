"""Deterministic interrater reliability.

PROVENANCE: Vendored from ethnographic-agentic-ai-auditor-workflows-q2-2026/
repos/codebook-plugin/lib/reliability.py (commit f62bcb66c71b918448f549a1ba626ec9f61715ff).
Licensed under MIT. Vendored fork; corpus_kb owns it; re-sync manually.

Why fork over krippendorff package: the m_u<2 exclusion (units with <2 raters
are dropped entirely, per the alignment-recovery rule) and the apps<5 unstable-
flag semantics are production dependencies the census baseline depends on; they
are not configurable in the maintained package. This fork is exact and locked
to those semantics.

Primary: per-code binary Krippendorff's alpha (nominal, coincidence-matrix
formula), computed exactly as:

    alpha_c = 1 - (n - 1) * sum_u[ n_u0 * n_u1 / (m_u - 1) ] / (n_0 * n_1)

for each code c, where each unit u is a coded segment ("turn"); n_u0/n_u1
are the counts of coders who did/did not apply code c to that segment;
m_u = n_u0 + n_u1 is the number of coders who actually rated that segment
for this code (a coder who returned no entry for a segment contributes
missing data, not a 0 -- units with fewer than 2 raters are excluded
entirely, since they contribute no pairable values). The same rule holds
one level down: a coder who returned an entry for a segment but was never
actually dispatched this specific code as a candidate (see `entry`'s
optional `rated_code_ids` in `_binary_units_for_code`) also contributes
missing data for that code, not a fabricated 0 -- indistinguishable
"rejected" and "never asked" would corrupt alpha with signal that was
never really observed. n = sum_u m_u; and
n_0/n_1 are the total 0/1 counts across all included units. Alpha is null
(None) when there is no variance (n_0 == 0 or n_1 == 0) or too little data
(n < 2) to define disagreement. A code is flagged `unstable` when it was
applied fewer than 5 times (apps < 5) among the included units.

Secondary: per-code binary Fleiss' kappa (generalized to allow a variable
number of raters per unit), for comparability with the prior run, plus a
pooled/overall alpha and kappa computed by pooling every code's per-segment
units into one coincidence matrix and applying the same formulas once.
"""

from __future__ import annotations


def _active_segment_ids(segments):
    """segment_ids that were actually dispatched for coding (excludes any
    marked `excluded`, e.g. sub_minimal turns). Returns None if `segments`
    is None, meaning "infer the universe from whatever appears in the
    sheets" rather than enforcing a fixed universe."""
    if segments is None:
        return None
    ids = []
    for seg in segments:
        if seg.get("excluded"):
            continue
        ids.append(seg.get("segment_id"))
    return ids


def _code_ids_present(sheets, code_universe=None):
    """Codes to report on: every code actually observed in `sheets`, UNIONED
    with `code_universe` if given. Without a universe, a code nobody ever
    applied never enters the returned set -- the single most extreme case
    of the `apps < 5` "unstable" condition is exactly the one silently
    skipped from the report instead of flagged, masking a whole code going
    unused/unseen across a batch. Callers should pass the full closed set
    of code ids (e.g. every code_id in the pinned codebook) so a
    zero-application code still gets a `per_code` entry (alpha/kappa=None,
    apps=0, unstable=True) rather than vanishing."""
    codes = set()
    for sheet in sheets:
        for entry in sheet.get("entries", []):
            for assignment in entry.get("assignments", []):
                codes.add(assignment.get("code_id"))
    if code_universe:
        codes |= set(code_universe)
    return codes


def _binary_units_for_code(sheets, segment_ids, code_id):
    """For one code, build the list of per-segment (n_u0, n_u1, m_u)
    triples -- one per segment that at least one coder actually returned an
    entry for. A coder who never returned an entry for a segment does not
    contribute a rating there (missing data, never treated as a 0).

    The same invariant extends to the per-code level: an entry may carry an
    optional `rated_code_ids` set naming exactly the codes that coder was
    actually dispatched (asked to decide) for that segment. When present, a
    code_id outside that set means the coder was never shown this candidate
    code -- missing data, not a 0 -- so the entry contributes no rating for
    it at all, even though the entry itself exists (the coder DID decide on
    other codes for that segment). Entries without `rated_code_ids` (e.g.
    hand-built test fixtures, or callers that can't reconstruct the
    dispatched set) keep the legacy behavior: any code absent from
    `assignments` is scored as an explicit 0."""
    ratings = {sid: [] for sid in segment_ids} if segment_ids is not None else {}

    for sheet in sheets:
        for entry in sheet.get("entries", []):
            sid = entry.get("segment_id")
            if segment_ids is not None and sid not in ratings:
                # Not part of the dispatched universe (e.g. an alignment
                # defect); reliability is computed on the intended universe
                # only, alignment issues are align.py's concern.
                continue
            if sid not in ratings:
                ratings[sid] = []
            rated_code_ids = entry.get("rated_code_ids")
            if rated_code_ids is not None and code_id not in rated_code_ids:
                # This coder was never dispatched this code for this
                # segment: missing data, contributes no rating.
                continue
            codes_applied = {a.get("code_id") for a in entry.get("assignments", [])}
            ratings[sid].append(1 if code_id in codes_applied else 0)

    units = []
    for values in ratings.values():
        m_u = len(values)
        if m_u < 2:
            continue
        n_u1 = sum(values)
        n_u0 = m_u - n_u1
        units.append((n_u0, n_u1, m_u))
    return units


def _alpha_from_units(units):
    """Returns (alpha_or_None, n, n_0, n_1)."""
    n = sum(m for (_u0, _u1, m) in units)
    n_0 = sum(u0 for (u0, _u1, _m) in units)
    n_1 = sum(u1 for (_u0, u1, _m) in units)
    if n_0 == 0 or n_1 == 0 or n < 2:
        return None, n, n_0, n_1
    term_sum = sum((u0 * u1) / (m - 1) for (u0, u1, m) in units)
    alpha = 1 - (n - 1) * term_sum / (n_0 * n_1)
    return alpha, n, n_0, n_1


def _kappa_from_units(units):
    """Generalized (variable raters-per-unit) Fleiss' kappa for the binary
    0/1 decision, secondary metric only. Returns None if there is no usable
    data."""
    if not units:
        return None
    n = sum(m for (_u0, _u1, m) in units)
    n_0 = sum(u0 for (u0, _u1, _m) in units)
    n_1 = sum(u1 for (_u0, u1, _m) in units)
    if n == 0:
        return None
    p_units = [(u1 * (u1 - 1) + u0 * (u0 - 1)) / (m * (m - 1)) for (u0, u1, m) in units]
    p_bar = sum(p_units) / len(p_units)
    p1 = n_1 / n
    p0 = n_0 / n
    p_bar_e = p1 * p1 + p0 * p0
    if p_bar_e >= 1:
        return None
    return (p_bar - p_bar_e) / (1 - p_bar_e)


# Vendored public API name (see PROVENANCE); kept verbatim for bundle
# call-site and test compatibility. N802 suppressed deliberately.
def computeReliability(sheets, segments=None, code_universe=None):  # noqa: N802
    """Compute per-code and pooled/overall reliability across a set of
    coder sheets that were dispatched (the same batch of) segments.

    M1 constraint: all sheets must have binary (0/1) decision per code per
    segment. Multi-label (a single segment can have multiple codes applied)
    is allowed, but multiple values for the same code on the same segment
    by the same coder is not supported in this version.

    Parameters
    ----------
    sheets : list of coder_sheet dicts.
    segments : optional list of segment dicts for the batch (defines the
        full universe of dispatched, codable segment_ids so that a missing
        sheet entry is treated as missing data under alpha, per the
        alignment-recovery rule). If omitted, the universe of segments is
        inferred from whatever segment_ids appear in the sheets.
    code_universe : optional iterable of every valid code_id (e.g. the full
        closed codebook). Codes in this set that no coder ever applied
        still get a `per_code` entry (alpha/kappa=None, apps=0,
        unstable=True) instead of being silently absent from the report.

    Returns dict:
        {
          "per_code": {code_id: {alpha, kappa, n, apps, unstable}},
          "overall": {"alpha": ..., "kappa": ...},
          "flags": [ "unstable:<code_id>", ... ],
        }

    Raises
    ------
    ValueError
        If any entry has multiple assignments for the same code (multi-label
        per code), which violates the binary-per-code assumption.
    """
    # Guard: M1 is binary-per-code only.
    for sheet in sheets:
        for entry in sheet.get("entries", []):
            code_counts = {}
            for assignment in entry.get("assignments", []):
                code_id = assignment.get("code_id")
                code_counts[code_id] = code_counts.get(code_id, 0) + 1
            for code_id, count in code_counts.items():
                if count > 1:
                    raise ValueError(
                        f"Multi-label input: entry for segment {entry.get('segment_id')} "
                        f"has {count} assignments for code {code_id}. "
                        f"M1 supports binary (0/1) decisions only."
                    )

    segment_ids = _active_segment_ids(segments)
    codes = _code_ids_present(sheets, code_universe)

    per_code = {}
    flags = []
    all_units = []
    for code_id in sorted(codes):
        units = _binary_units_for_code(sheets, segment_ids, code_id)
        alpha, n, _n_0, n_1 = _alpha_from_units(units)
        kappa = _kappa_from_units(units)
        apps = n_1
        unstable = apps < 5
        per_code[code_id] = {
            "alpha": alpha,
            "kappa": kappa,
            "n": n,
            "apps": apps,
            "unstable": unstable,
        }
        if unstable:
            flags.append(f"unstable:{code_id}")
        all_units.extend(units)

    overall_alpha, _n, _n0, _n1 = _alpha_from_units(all_units)
    overall_kappa = _kappa_from_units(all_units)

    return {
        "per_code": per_code,
        "overall": {"alpha": overall_alpha, "kappa": overall_kappa},
        "flags": flags,
    }

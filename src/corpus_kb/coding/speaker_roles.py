"""Speaker-role classification for transcript chunks.

Deterministic heuristic based on speaker label frequency across the corpus.
Ported from the fieldwork exemplar at:
~/Documents/Research/midship-non-controls-ops-audit-q3-2026/analysis/extract_speaker_roles.py

A speaker whose exact label appears in >= VENDOR_THRESHOLD distinct documents
corpus-wide is classified as staff (moderator). A speaker matching the known
participant roster is classified as a participant, unless they collide with a
high-volume staff label (ambiguous collision -> unknown). Everything else is
unknown (deferred to human/subagent).
"""

from __future__ import annotations


def classify_role(
    speaker: str,
    doc_count: int,
    roster: set[str],
    vendor_threshold: int = 8,
) -> str:
    """Classify a speaker as moderator, participant, or unknown.

    Args:
        speaker: The speaker label (e.g. "Interviewer", "Dana Okafor").
        doc_count: How many distinct documents contain this exact speaker label.
        roster: Set of lowercased participant names (full + first-token).
        vendor_threshold: Speaker seen in >= N distinct docs => moderator (default 8).

    Returns:
        One of: "moderator", "participant", "unknown".
    """
    speaker = speaker.strip()
    if not speaker:
        return "unknown"

    key = speaker.lower()

    # Check roster match (participant unless ambiguous collision)
    if key in roster or key.split()[0] in roster:
        # A named participant is a participant even if a first name collides,
        # but a high-volume exact label is ambiguous (bare "Michael" vs name).
        if doc_count >= vendor_threshold and " " not in speaker:
            return "unknown"  # ambiguous collision, defer
        return "participant"

    # Check moderator threshold (high-volume speaker)
    if doc_count >= vendor_threshold:
        return "moderator"

    return "unknown"

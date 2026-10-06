from __future__ import annotations


def find_collisions(claims: dict[str, list[str]]) -> list[str]:
    """Find inclusion terms claimed by more than one code.

    Args:
        claims: Dict mapping term -> list of code_ids that claim it

    Returns:
        List of terms claimed by >1 code
    """
    return [term for term, codes in claims.items() if len(codes) > 1]


def over_df_cap(hit_chunks: int, corpus_chunks: int, cap_frac: float = 0.012) -> bool:
    """Check if hit ratio exceeds the document frequency cap.

    Args:
        hit_chunks: Number of chunks matching the term
        corpus_chunks: Total number of chunks in corpus
        cap_frac: Maximum allowed fraction (default 0.012, i.e., 1.2%)

    Returns:
        True if hit_chunks / corpus_chunks > cap_frac
    """
    return hit_chunks / corpus_chunks > cap_frac


def criterial_owner(term: str, code_criteria: dict[str, str]) -> str | None:
    """Find the single code whose inclusion_criteria contains the term.

    Searches only the inclusion_criteria text, never examples or other fields.
    Returns None if zero or multiple codes match.

    Args:
        term: The keyword term to search for
        code_criteria: Dict mapping code_id -> inclusion_criteria text

    Returns:
        The code_id if exactly one match found, None otherwise
    """
    matches = [code_id for code_id, criteria in code_criteria.items() if term in criteria]

    if len(matches) == 1:
        return matches[0]
    return None

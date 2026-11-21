"""Op to method-id attribution for the telemetry events (issue #442).

A FIXED table maps every register op to its method id, so a new shared
op fails loudly (the tests pin the 18 rows) instead of mis-attributing.
On master 12 of the 18 register ops have one method each (exact
attribution); ``keyphrases_extract`` is the one shared op with a
discriminator — its ``variant`` kwarg picks 05/06/07 (the three
``SUPPORTED_VARIANTS`` in ``tools/keyphrase_tools.py``; the spec default
``tfidf`` applies when the caller omits the kwarg). The remaining five
shared ops (``analyze_run`` 02/03/22, ``labeling_build`` 04/19,
``topics_build`` 09/10/11, ``classification_build`` 16/17,
``sentiment_screen`` 26/27) have no discriminator parameter and map to
``None``: ``emit`` skips them, and the report header declares them a
known-imperfect bucket (owner decision D-3).

Pure data and one pure function; nothing here imports tools or api, so
every surface adapter can import this module downward.
"""

from __future__ import annotations

from typing import Any

METHOD_FOR_OP: dict[str, str | None] = {
    "analyze_run": None,  # methods 02/03/22 — shared, no discriminator
    "labeling_build": None,  # methods 04/19
    "topics_build": None,  # methods 09/10/11
    "keyphrases_extract": None,  # methods 05/06/07 — see method_id_for_op
    "clusters_inspect": "12",
    "centroids_classify": "13",
    "neighbors_classify": "14",
    "lexical_classify": "15",
    "classification_build": None,  # methods 16/17
    "nli_classify": "18",
    "active_learn_next": "20",
    "search": "23",
    "ask": "24",
    "ingest_text": "25",
    "sentiment_screen": None,  # methods 26/27 — no discriminator param
    "contradictions_screen": "28",
    "duplicates_screen": "29",
    "outliers_rank": "30",
    "survey_openend_codes": "31",
    "usability_severity": "32",
    "emotion_tag": "33",
}

# keyphrases_extract variant -> method id (SUPPORTED_VARIANTS order).
_KEYPHRASE_VARIANTS: dict[str, str] = {
    "tfidf": "05",
    "embedding": "06",
    "llm": "07",
}

_KEYPHRASES_OP = "keyphrases_extract"
_KEYPHRASE_DEFAULT_VARIANT = "tfidf"


def method_id_for_op(op_name: str, kwargs: dict[str, Any]) -> str | None:
    """The method id for one op invocation, or ``None`` when it cannot be known.

    Single-method ops return their pinned id; ``keyphrases_extract``
    returns the id its resolved ``variant`` names (missing kwarg = the
    ``tfidf`` spec default, F13); an unknown variant or a shared op with
    no discriminator returns ``None`` so ``emit`` skips the event.
    """
    if op_name == _KEYPHRASES_OP:
        variant = kwargs.get("variant", _KEYPHRASE_DEFAULT_VARIANT)
        return _KEYPHRASE_VARIANTS.get(str(variant))
    return METHOD_FOR_OP.get(op_name)

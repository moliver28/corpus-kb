"""The doctor journey checks (novice-UX program W3).

``corpus-kb doctor`` tells the novice WHERE they are in the analytics
progression and the ONE next step with its exact command. The journey is
expressed as ORDINARY doctor checks (``journey_state``, ``journey_next``)
in the existing check structure — no new envelope keys, no catalogue
rows. One probe module behind both surfaces: the CLI prints the section,
the SPA snapshot carries the same checks in ``research_checks``.

A method is RUNNABLE when every prerequisite product exists and every
probed input is present. The NEXT STEP is the lowest-difficulty runnable
build method whose product does not exist yet (ties break by method id);
an empty corpus points at ingest, a vector-less corpus points at the
re-embed fix. Same state in, same bytes out. Degraded storage or a broken
register yields one honest omitted check — never a wrong suggestion.

The probed state matrix lives in ``doctor_journey_state``; the encoded
op/command and build-product bridges live in ``doctor_journey_maps``.
"""

from __future__ import annotations

from typing import Any

from corpus_kb._setup.doctor_engine import resolved_backend
from corpus_kb._setup.doctor_journey_maps import (
    BUILD_PRODUCTS,
    INPUT_STATE,
    OP_CLI,
    UNPROBED_INPUTS,
)
from corpus_kb._setup.doctor_journey_state import JourneyState, read_build_state
from corpus_kb._setup.doctor_research import (
    DOC_GETTING_STARTED,
    REEMBED_FIX,
    STATUS_INFO,
    STATUS_OK,
    STATUS_SKIPPED,
    ResearchCheck,
)
from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_progression import DIFFICULTY_LEVELS

__all__ = [
    "build_journey_checks",
    "journey_checks",
    "print_journey_checks",
]

JOURNEY_DOC = "docs/ANALYSIS-METHODS.md"
SETUP_FIX = "corpus-kb setup"
INGEST_FIX = "corpus-kb research ingest"

_DIFFICULTY_RANK = {level: index for index, level in enumerate(DIFFICULTY_LEVELS)}


def _join_words(words: list[str]) -> str:
    if len(words) <= 1:
        return words[0] if words else ""
    if len(words) == 2:
        return f"{words[0]} and {words[1]}"
    return f"{', '.join(words[:-1])}, and {words[-1]}"


def _missing(
    record: Any, register: MethodRegister, state: JourneyState, ollama_ok: bool
) -> list[str]:
    """The state keys (or 'llm') this method still needs, in order."""
    missing: list[str] = []
    by_id = {row.id: row for row in register.methods}
    for pid in record.prerequisites or []:
        prereq = by_id.get(pid)
        key = BUILD_PRODUCTS.get(prereq.op, (None, ""))[0] if prereq and prereq.op else None
        if key is None or getattr(state, key) == 0:
            missing.append(key or f"method {pid}")
    for kind in record.inputs_required:
        if kind == "llm":
            if not ollama_ok:
                missing.append("llm")
        elif kind not in UNPROBED_INPUTS:
            key = INPUT_STATE[kind]
            if getattr(state, key) == 0:
                missing.append(key)
    return list(dict.fromkeys(missing))


def _runnable_sorted(register: MethodRegister, state: JourneyState, ollama_ok: bool) -> list[Any]:
    """Implemented methods that can run now, easiest first."""
    rows = [row for row in register.methods if row.op and row.op in OP_CLI]
    ready = [row for row in rows if not _missing(row, register, state, ollama_ok)]
    return sorted(ready, key=lambda row: (_DIFFICULTY_RANK.get(row.difficulty or "", 9), row.id))


def _state_check(state: JourneyState, runnable: list[Any]) -> ResearchCheck:
    if state.vectors == 0:
        head = f"You have {state.chunks} chunks without vectors."
    else:
        head = f"You have {state.chunks} chunks and {state.vectors} vectors."
    sentences = [head]
    if runnable:
        easiest = runnable[0]
        sentences.append(f"You can run {len(runnable)} methods today.")
        sentences.append(f"The easiest is {easiest.name} (method {easiest.id}).")
        rungs = [
            level for level in DIFFICULTY_LEVELS if any(r.difficulty == level for r in runnable)
        ]
        sentences.append(f"You can start the {_join_words(rungs)} levels today.")
    else:
        sentences.append("No registered method can run yet.")
    return ResearchCheck(
        name="journey_state",
        status=STATUS_OK if runnable else STATUS_INFO,
        detail=" ".join(sentences),
        doc=JOURNEY_DOC,
    )


def _next_check(
    register: MethodRegister, implemented: list[Any], state: JourneyState, ollama_ok: bool
) -> ResearchCheck:
    if state.vectors == 0:
        reembed = REEMBED_FIX[:1].upper() + REEMBED_FIX[1:]
        return ResearchCheck(
            name="journey_next",
            status=STATUS_OK,
            detail=f"Next: build vectors. {reembed}.",
            fix=REEMBED_FIX,
            doc=DOC_GETTING_STARTED,
        )
    builds = [
        row
        for row in _runnable_sorted(register, state, ollama_ok)
        if row.op in BUILD_PRODUCTS and getattr(state, BUILD_PRODUCTS[row.op][0]) == 0
    ]
    if not builds:
        return ResearchCheck(
            name="journey_next",
            status=STATUS_INFO,
            detail=(
                "You built every analysis artifact this version stores. "
                "Screen and query the corpus any time."
            ),
            doc=JOURNEY_DOC,
        )
    nxt = builds[0]
    key, action = BUILD_PRODUCTS[nxt.op]
    sentences = [f"Next: {action} with method {nxt.id}.", f"Run: corpus-kb {OP_CLI[nxt.op]}."]
    unlock = sorted(
        row.id
        for row in implemented
        if row is not nxt and _missing(row, register, state, ollama_ok) == [key]
    )
    if unlock:
        sentences.append(f"It unlocks methods {_join_words(unlock)}.")
    return ResearchCheck(
        name="journey_next",
        status=STATUS_OK,
        detail=" ".join(sentences),
        fix=f"corpus-kb {OP_CLI[nxt.op]}",
        doc=JOURNEY_DOC,
    )


def build_journey_checks(
    register: MethodRegister, state: JourneyState, ollama_ok: bool
) -> list[ResearchCheck]:
    """The pure journey matrix: register + probed state -> doctor checks."""
    if state.chunks == 0:
        return [
            ResearchCheck(
                name="journey_state",
                status=STATUS_INFO,
                detail="Your corpus is empty. Ingest documents to begin.",
                doc=DOC_GETTING_STARTED,
            ),
            ResearchCheck(
                name="journey_next",
                status=STATUS_INFO,
                detail="Next: ingest documents. Run: corpus-kb research ingest.",
                fix=INGEST_FIX,
                doc=DOC_GETTING_STARTED,
            ),
        ]
    implemented = sorted(
        (row for row in register.methods if row.op and row.op in OP_CLI), key=lambda row: row.id
    )
    runnable = _runnable_sorted(register, state, ollama_ok)
    return [_state_check(state, runnable), _next_check(register, implemented, state, ollama_ok)]


def _unavailable(status: str, detail: str, fix: str = "", doc: str = "") -> ResearchCheck:
    """One honest omitted-journey check with its reason."""
    return ResearchCheck(name="journey", status=status, detail=detail, fix=fix, doc=doc)


async def journey_checks(
    config: dict[str, Any], conn_str: str, db_ok: bool, ollama_ok: bool
) -> list[ResearchCheck]:
    """The journey section for one doctor run (never raises)."""
    if not db_ok:
        return [
            _unavailable(
                STATUS_SKIPPED,
                "The database did not answer. The journey is omitted.",
                fix=SETUP_FIX,
            )
        ]
    try:
        register = load_method_register(config)
    except Exception:
        return [
            _unavailable(
                STATUS_INFO,
                "The method register did not load. The journey is omitted.",
                doc=JOURNEY_DOC,
            )
        ]
    try:
        state = await read_build_state(conn_str, resolved_backend(config))
    except Exception:
        return [
            _unavailable(
                STATUS_INFO,
                "The analysis tables did not answer. The journey is omitted.",
            )
        ]
    if state is None:
        return [
            _unavailable(
                STATUS_INFO,
                "The analysis tables are not initialized. Run corpus-kb setup, "
                "then run corpus-kb doctor again.",
                fix=SETUP_FIX,
            )
        ]
    return build_journey_checks(register, state, ollama_ok)


def print_journey_checks(checks: list[ResearchCheck]) -> None:
    """Print the journey section (ASCII only for cp1252 consoles)."""
    print("\nYour analysis journey:")
    for check in checks:
        print(f"  {check.name}: {check.status.upper()} - {check.detail}")
        if check.fix:
            print(f"    fix: {check.fix}")
        if check.doc:
            print(f"    docs: {check.doc}")

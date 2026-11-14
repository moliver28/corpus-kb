"""The ``next_methods`` envelope key for the question router (novice-UX W3c).

A resolved answer hands the novice their next run: up to three register
rows the chosen method's ``next:`` chain points at, easiest first. The
order is ascending difficulty (novice, intermediate, advanced), ties
break by method id; the cap matches the suggestions chips. Each item is
``{method_id, title, why, run}``: the register id, the register name
verbatim, one plain sentence over the chain edge and the target's
difficulty, and the ``corpus-kb`` command.

The command derives from ``surface_registry``'s ``cli_path`` — the one
source the doctor journey pins its own map against. The registry lists
no row for ``ingest_text``, so one fallback entry bridges that gap
(mirroring the doctor branch's ``OP_CLI``; reconcile on merge). A
target with no command at all is skipped, never guessed.

Presence follows the run, not the chain: a degraded or ambiguous answer
carries ``[]`` — the router did not decide, so it does not hand-hold.
The key is always present, so ``[]`` is an honest shape, never a
silently dropped key.
"""

from __future__ import annotations

from ..analysis.method_register import MethodRecord, MethodRegister
from ..analysis.method_register_progression import DIFFICULTY_LEVELS
from ..surface_registry import SURFACES

__all__ = ["_NEXT_CAP", "attach_next_methods", "cli_run_hint", "next_methods_for"]

_NEXT_CAP = 3

# op -> CLI path for the one implemented op the registry does not list.
# The doctor journey (feature/novice-journey) carries the same bridge in
# its ``OP_CLI`` map; keep the two rows in step at merge time.
_OP_CLI_FALLBACK: dict[str, str] = {"ingest_text": "ingest-text"}

_DIFFICULTY_RANK: dict[str | None, int] = {
    **{level: index for index, level in enumerate(DIFFICULTY_LEVELS)},
    None: len(DIFFICULTY_LEVELS),
}


def cli_run_hint(op: str | None) -> str | None:
    """The ``corpus-kb`` command for this op, or ``None`` when none exists.

    The registry is the source; the fallback map covers only the ops it
    does not list. A ``None`` op (human-led rows) never yields a hint.
    """
    if op is None:
        return None
    for surface in SURFACES:
        if surface.mcp_tool == op and "cli" in surface.surfaces:
            return f"corpus-kb {surface.cli_path}"
    fallback = _OP_CLI_FALLBACK.get(op)
    if fallback is None:
        return None
    return f"corpus-kb {fallback}"


def _row_for(register: MethodRegister, method_id: str) -> MethodRecord | None:
    """The register row with this id, or ``None`` (the loader validates)."""
    for record in register.methods:
        if record.id == method_id:
            return record
    return None


def _why(target: MethodRecord) -> str:
    """One plain sentence over the chain edge and the target's difficulty."""
    if target.difficulty is None:
        return f"Build on this method with {target.name}."
    return f"Build on this method with {target.name} ({target.difficulty} level)."


def next_methods_for(register: MethodRegister, record: MethodRecord | None) -> list[dict[str, str]]:
    """The chosen row's chain as envelope items, easiest first, capped.

    Targets the register cannot resolve or cannot turn into a command
    are skipped; the rest sort by ascending difficulty then id.
    """
    if record is None or not record.next:
        return []
    items: list[tuple[int, str, dict[str, str]]] = []
    for method_id in record.next:
        target = _row_for(register, method_id)
        if target is None:
            continue
        run = cli_run_hint(target.op)
        if run is None:
            continue
        item = {
            "method_id": target.id,
            "title": target.name,
            "why": _why(target),
            "run": run,
        }
        items.append((_DIFFICULTY_RANK[target.difficulty], target.id, item))
    items.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in items[:_NEXT_CAP]]


def attach_next_methods(
    envelope: dict[str, object], register: MethodRegister, record: MethodRecord | None
) -> None:
    """Set ``next_methods``: the chain only on a resolved answer.

    The caller passes the record whose chain rides: the row matching the
    intent's chosen id on the automatic path, the override record on the
    override path. Every other status carries ``[]``.
    """
    if envelope.get("status") != "resolved":
        envelope["next_methods"] = []
        return
    envelope["next_methods"] = next_methods_for(register, record)

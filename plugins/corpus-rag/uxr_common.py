"""Shared helpers for the ``corpus-rag uxr`` command group (issue #355).

The error type, the payload readers, and the two ways a uxr command
finds its study: the ``-p`` flag, or the folder's ``corpus.json`` slug.

Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

import uxr_store

UXR_POST_SEARCH = "uxr post-search"


class UxrError(Exception):
    """A user-facing uxr command failure with an exit code."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def slug_for(args) -> str:
    """The study slug: ``-p`` first, else the folder's corpus.json slug."""
    slug = getattr(args, "study_slug", None)
    if slug:
        return uxr_store.slugify(str(slug))
    import folder_state

    slug = folder_state.folder_state(scan=False).slug or ""
    return uxr_store.slugify(slug) if slug else "default"


def root_of(args) -> Path:
    """The study folder: ``--dir`` when given, else the working folder."""
    root = getattr(args, "dir", None)
    return Path(root).resolve() if root else Path.cwd()


def json_payload(payload: str, name: str) -> list[dict]:
    """The command's JSON argument: inline text, or ``@file``."""
    text = payload
    if payload.startswith("@"):
        try:
            text = Path(payload[1:]).read_text(encoding="utf-8")
        except OSError as exc:
            raise UxrError(f"cannot read {name} payload file: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UxrError(f"the {name} payload is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise UxrError(f"the {name} payload must be a JSON list.")
    return [row for row in data if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def add_uxr_subparser(subs) -> None:
    """The ``uxr`` group on the corpus-rag parser (issue #355)."""
    uxr = subs.add_parser(
        "uxr", help="UXR study layer: consent, plan, classify, quotes, rows, rollup."
    )
    uxr_subs = uxr.add_subparsers(dest="uxr_command", required=True)

    uxr_subs.add_parser("consent", help="Report the setup consent answer; exit 1 means local only.")

    uplan = uxr_subs.add_parser(
        "ingest-plan", help="Store the study and its question ids from one spec."
    )
    uplan.add_argument("--spec", required=True, help="The UX1-N.spec.json file")
    uplan.add_argument("-p", "--study-slug", default=None, help="The study slug")

    ucls = uxr_subs.add_parser("classify", help="Sort files into classes; no model call.")
    ucls.add_argument("--dir", default=None, help="The study folder (default: here)")
    ucls.add_argument("--roster", default=None, help="The participant roster file")
    ucls.add_argument("--staff", default=None, help="The staff list file")
    ucls.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uconf = uxr_subs.add_parser(
        "confirm-classes", help="Store confirmed classes; sessions and meetings only."
    )
    uconf.add_argument("payload", help="The classes JSON, or @file")
    uconf.add_argument("-p", "--study-slug", default=None, help="The study slug")

    ustubs = uxr_subs.add_parser(
        "stubs", help="Write sources/corpus stubs so [S:slug] cites resolve."
    )
    ustubs.add_argument("--dir", default=None, help="The study folder (default: here)")
    ustubs.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uquote = uxr_subs.add_parser("check-quote", help="exact, near_miss, or fail; exit 1 on fail.")
    uquote.add_argument("--quote", required=True, help="The quote text")
    uquote.add_argument("--unit-text", default=None, help="The unit text to check against")
    uquote.add_argument("--unit-file", default=None, help="A file holding the unit text")

    urows = uxr_subs.add_parser(
        "validate-rows", help="Check stance rows; failed rows stay unverified."
    )
    urows.add_argument("payload", help="The rows JSON, or @file")
    urows.add_argument("--store", action="store_true", help="Store the rows after the checks")
    urows.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uroll = uxr_subs.add_parser("rollup", help="Count stances per question; researcher lines win.")
    uroll.add_argument(
        "--max-unverified",
        type=float,
        default=0.1,
        help="The unverified share limit (default 0.1)",
    )
    uroll.add_argument("--dir", default=None, help="The study folder (default: here)")
    uroll.add_argument("-p", "--study-slug", default=None, help="The study slug")

    ucontract = uxr_subs.add_parser(
        "contract-test", help="Compare the rollup with the report tool output."
    )
    ucontract.add_argument(
        "--evidence-file", default=None, help="A file with the evidence-weight JSON"
    )
    ucontract.add_argument("--dir", default=None, help="The study folder (default: here)")
    ucontract.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uthemes = uxr_subs.add_parser(
        "write-themes", help="Write themes between the corpus markers only."
    )
    uthemes.add_argument("--narrative-file", required=True, help="The narrative text file")
    uthemes.add_argument("--dir", default=None, help="The study folder (default: here)")
    uthemes.add_argument("-p", "--study-slug", default=None, help="The study slug")

    usummary = uxr_subs.add_parser(
        "apply-summary", help="Write a marked block into each session summary."
    )
    usummary.add_argument("--dir", default=None, help="The study folder (default: here)")
    usummary.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uins = uxr_subs.add_parser("insights", help="Stored rows, rollup, and coverage counts.")
    uins.add_argument("--question", default=None, help="One question id, like H1")
    uins.add_argument("--stance", default=None, help="One stance value")
    uins.add_argument("--session", default=None, help="One source slug")
    uins.add_argument("--format", choices=("text", "json"), default="text", help="Output format")
    uins.add_argument("-p", "--study-slug", default=None, help="The study slug")

    uimp = uxr_subs.add_parser("import", help="One-time copy from a 0.6.0 uxr schema; read-only.")
    uimp.add_argument("-p", "--study-slug", default=None, help="The study slug")

    upost = uxr_subs.add_parser(
        "post-search", help="Internal: the post_search extension point command."
    )
    upost.add_argument(
        "--post-search-json",
        default=None,
        help='The search context, like {"slug": "..."}',
    )

"""The setup questions: AskUserQuestion payloads, four per call.

``corpus-rag setup --questions`` prints one JSON payload:

    {"calls": [[4 questions], [up to 4 questions]],
     "pii_hits": [...], "preflight": {"soft": [...]}}

Each question carries ``question``, ``header`` (12 characters max),
``options`` (label + description), and ``multiSelect``. The setup skill
asks them unchanged and saves the answers keyed by question header.

Question list (issue #353): study name (slug), watch folders, backend,
consent for a cloud model, auto ingest, permission allow rules, queue
autorun. Stdlib only.
"""

from __future__ import annotations

import study_registry

PER_CALL = 4
YES_NO = (
    {"label": "Yes", "description": "Turn it on."},
    {"label": "No", "description": "Leave it off."},
)

HEADER_STUDY = "Study name"
HEADER_WATCH = "Folders"
HEADER_BACKEND = "Backend"
HEADER_CONSENT = "Cloud model"
HEADER_AUTO = "Auto ingest"
HEADER_RULES = "Permissions"
HEADER_AUTORUN = "Autorun"

HEADERS = (
    HEADER_STUDY,
    HEADER_WATCH,
    HEADER_BACKEND,
    HEADER_CONSENT,
    HEADER_AUTO,
    HEADER_RULES,
    HEADER_AUTORUN,
)


def _question(text: str, header: str, options: list[dict], multi: bool = False) -> dict:
    return {
        "question": text,
        "header": header,
        "options": options,
        "multiSelect": multi,
    }


def study_question(folder_name: str) -> dict:
    slug = study_registry.slugify(folder_name)
    return _question(
        "What is the study name (a slug)? It names the database and the study.",
        HEADER_STUDY,
        [
            {
                "label": slug,
                "description": f"From the folder name '{folder_name}'.",
            },
            {
                "label": "Other",
                "description": "Type the slug you want (letters, numbers, underscores).",
            },
        ],
    )


def watch_question(subfolders: list[str]) -> dict:
    options = [{"label": ".", "description": "The whole folder."}]
    for name in subfolders[:3]:
        options.append(
            {"label": name, "description": f"The '{name}' subfolder."}
        )
    return _question(
        "Which folders should the study watch for files?",
        HEADER_WATCH,
        options,
        multi=True,
    )


def backend_question() -> dict:
    return _question(
        "Which storage backend should this study use?",
        HEADER_BACKEND,
        [
            {
                "label": "postgres",
                "description": "Postgres with pgvector. One database per study.",
            },
            {
                "label": "duckdb",
                "description": "One local file. Single-user, no server.",
            },
        ],
    )


def consent_question() -> dict:
    return _question(
        (
            "May a cloud model ever see study text? Answering No keeps this "
            "study local only; no transcript text leaves the machine."
        ),
        HEADER_CONSENT,
        [
            {"label": "No", "description": "Local only. Nothing leaves this machine."},
            {"label": "Yes", "description": "Cloud models may process study text."},
        ],
    )


def auto_ingest_question() -> dict:
    return _question(
        "Should new files in the watched folders queue and ingest on their own?",
        HEADER_AUTO,
        YES_NO,
    )


def rules_question() -> dict:
    return _question(
        (
            "Add permission allow rules for corpus-rag search, ask, and status "
            "to this folder's .claude/settings.json?"
        ),
        HEADER_RULES,
        [
            {"label": "Yes", "description": "Adds three read-only allow rules."},
            {"label": "No", "description": "Ask for permission each time."},
        ],
    )


def autorun_question() -> dict:
    return _question(
        (
            "Turn on queue.autorun in the config overlay? This is global: it "
            "applies to every study on this machine."
        ),
        HEADER_AUTORUN,
        [
            {"label": "Yes", "description": "The engine drains its queue on its own."},
            {"label": "No", "description": "You run corpus-rag drain when you want."},
        ],
    )


def calls(folder_name: str, subfolders: list[str], backend: str | None = None) -> list[list[dict]]:
    """The question list in calls of four. ``backend`` pre-answers one."""
    questions = [
        study_question(folder_name),
        watch_question(subfolders),
        backend_question(),
        consent_question(),
        auto_ingest_question(),
        rules_question(),
        autorun_question(),
    ]
    if backend:
        questions[2] = dict(questions[2])
        questions[2]["answered"] = backend
    grouped = []
    for start in range(0, len(questions), PER_CALL):
        grouped.append(questions[start : start + PER_CALL])
    return grouped


def payload(
    folder_name: str,
    subfolders: list[str],
    pii_rows: list[dict],
    soft: list[dict],
    backend: str | None = None,
) -> dict:
    """The full --questions JSON payload."""
    return {
        "calls": calls(folder_name, subfolders, backend),
        "pii_hits": pii_rows,
        "preflight": {"soft": soft},
    }

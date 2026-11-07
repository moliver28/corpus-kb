"""The ``corpus-rag uxr`` command group: study setup and evidence (#355).

Storage is one SQLite file per study under
``${CLAUDE_PLUGIN_DATA}/uxr/``; engine reads go through the read-only
``corpus-rag sql`` route only; no UXR code ever writes to a study
database. Analysis-side handlers live in ``uxr_analysis`` and
``uxr_summaries``.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import gate_state
import op_client
import post_search
import study_commands
import study_server
import uxr_classify
import uxr_files
import uxr_store
from uxr_common import UXR_POST_SEARCH, UxrError, json_payload, root_of, slug_for

SPEC_NAME = re.compile(r"^([A-Z][A-Z0-9]+)-(\d+)\.spec\.json$")


# ---------------------------------------------------------------------------
# Engine read seams (tests monkeypatch these; both are read-only)
# ---------------------------------------------------------------------------


def engine_document_count(slug_arg: str | None) -> int | None:
    """The study's document count, or None when the engine is unreachable."""
    try:
        _slug, study = study_commands.scoped_study_off(slug_arg)
        stats = op_client.get_json(study_server.base_url(study) + "/api/stats", timeout=30)
        documents = stats.get("documents")
        return int(documents) if documents is not None else None
    except Exception:
        return None


_LEGACY_SQL = (
    "SELECT session, unit_id, stance, quote, quote_class, rationale FROM uxr.session_insight"
)


def fetch_legacy_rows(slug_arg: str | None) -> list[dict] | None:
    """The 0.6.0 ``uxr`` schema rows over the read-only SQL route."""
    try:
        _slug, study = study_commands.scoped_study_off(slug_arg)
        envelope = op_client.post_json(
            study_server.base_url(study) + "/api/query/sql",
            {"sql": _LEGACY_SQL},
            timeout=60,
        )
    except Exception:
        return None
    result = envelope.get("result")
    if not isinstance(result, list):
        return None
    return [row for row in result if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# consent, ingest-plan
# ---------------------------------------------------------------------------


def cmd_uxr_consent(_args) -> int:
    """Report the setup consent answer; unknown means local only."""
    entry = gate_state.folder_entry(Path.cwd())
    if entry.get("local_only") is False:
        print("Consent: cloud models may process study text.")
        return 0
    if entry.get("local_only") is True:
        print("Consent: local only. No study text may go to a cloud model.")
        return 1
    print("No consent answer on record for this folder; treating it as local only.")
    print("Consent: local only. No study text may go to a cloud model.")
    return 1


def spec_questions(data: dict) -> list[dict]:
    """RQn by position, Hn from the label prefix, An by position."""
    out: list[dict] = []
    for position, text in enumerate(_strings(data.get("research_questions")), 1):
        out.append({"id": f"RQ{position}", "kind": "rq", "position": position, "label": text})
    for position, entry in enumerate(data.get("hypotheses") or [], 1):
        label, text = _hypothesis_label(entry)
        out.append(
            {"id": label or f"H{position}", "kind": "h", "position": position, "label": text}
        )
    for position, text in enumerate(_strings(data.get("analysis_questions")), 1):
        out.append({"id": f"A{position}", "kind": "a", "position": position, "label": text})
    return out


def _strings(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


_HYP_PREFIX = re.compile(r"^(H\d+)\s*[:\-—]\s*(.+)$")


def _hypothesis_label(entry: object) -> tuple[str | None, str]:
    if isinstance(entry, dict):
        label = entry.get("label")
        text = str(entry.get("text") or "")
        return (str(label) if isinstance(label, str) and label.strip() else None, text)
    text = str(entry or "")
    match = _HYP_PREFIX.match(text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, text


def cmd_uxr_ingest_plan(args) -> int:
    """Store the study and its question ids, keyed by the spec id."""
    spec = Path(args.spec)
    try:
        raw = spec.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise UxrError(f"cannot read the spec file: {exc}") from exc
    except ValueError as exc:
        raise UxrError(f"the spec file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UxrError("the spec file must hold a JSON object.")
    spec_id = hashlib.sha256(raw).hexdigest()[:16]
    slug = slug_for(args)
    ticket = str(data.get("ticket") or "").strip()
    if not ticket:
        match = SPEC_NAME.match(spec.name)
        ticket = match.group(1) if match else slug
    questions = spec_questions(data)
    conn = uxr_store.connect(slug)
    uxr_store.save_study(conn, spec_id=spec_id, slug=slug, ticket=ticket, questions=questions)
    post_search.register(UXR_POST_SEARCH)
    print(f"ok: stored study '{slug}' (ticket {ticket}, spec id {spec_id}).")
    print("Question ids: " + ", ".join(q["id"] for q in questions))
    return 0


# ---------------------------------------------------------------------------
# classify, confirm-classes, stubs, check-quote
# ---------------------------------------------------------------------------


def cmd_uxr_classify(args) -> int:
    """Sort the study folder's files; no model call, unmatched shown."""
    root = root_of(args)
    roster = uxr_classify.load_names(Path(args.roster) if args.roster else None)
    staff = uxr_classify.load_names(Path(args.staff) if args.staff else None)
    rows = uxr_classify.classify_dir(root, roster=roster, staff=staff)
    for row in rows:
        speakers = ", ".join(row["speakers"]) or "-"
        flag = " (a line may hide a speaker)" if row["uncertain"] else ""
        print(f"{row['class']}\t{row['path']}\tspeakers: {speakers}{flag}")
    unmatched = sum(1 for row in rows if row["class"] == uxr_classify.UNMATCHED)
    if unmatched:
        print(f"{unmatched} unmatched file(s). Show them to the researcher; never guess a class.")
    payload = [{"path": row["path"], "class": row["class"]} for row in rows]
    print("payload: " + json.dumps(payload))
    return 0


def cmd_uxr_confirm_classes(args) -> int:
    """Store confirmed classes; only sessions and meetings reach source."""
    rows = json_payload(args.payload, "confirm-classes")
    conn = uxr_store.connect(slug_for(args))
    stored = 0
    skipped = 0
    for row in rows:
        path = str(row.get("path") or "")
        class_ = str(row.get("class") or "")
        if class_ not in (uxr_classify.PARTICIPANT, uxr_classify.MEETING):
            skipped += 1
            continue
        uxr_store.upsert_source(conn, uxr_store.slugify(Path(path).stem), path, class_)
        stored += 1
    print(
        f"ok: {stored} source(s) stored, {skipped} skipped "
        "(only participant_session and internal_meeting reach source)."
    )
    return 0


def cmd_uxr_stubs(args) -> int:
    """Write one ``sources/corpus/<slug>.md`` per source. Never overwrite."""
    root = root_of(args)
    conn = uxr_store.connect(slug_for(args))
    dated = []
    for source in uxr_store.sources(conn):
        row = dict(source)
        path = root / str(source.get("path") or "")
        try:
            row["artifact_date"] = date.fromtimestamp(path.stat().st_mtime).isoformat()
        except OSError:
            row["artifact_date"] = source.get("artifact_date") or ""
        dated.append(row)
    written = uxr_files.write_stubs(root, dated, date.today().isoformat())
    skipped = len(dated) - len(written)
    print(f"ok: {len(written)} stub(s) written, {skipped} already there (stubs never overwrite).")
    return 0


def cmd_uxr_check_quote(args) -> int:
    """exact, near_miss, or fail; exit 1 on fail."""
    import uxr_quotes

    if bool(args.unit_text) == bool(args.unit_file):
        raise UxrError("give exactly one of --unit-text or --unit-file.")
    if args.unit_file:
        try:
            unit_text = Path(args.unit_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UxrError(f"cannot read the unit file: {exc}") from exc
    else:
        unit_text = str(args.unit_text or "")
    verdict = uxr_quotes.check_quote(args.quote, unit_text)
    print(f"check-quote: {verdict}")
    return 0 if verdict != uxr_quotes.FAIL else 1

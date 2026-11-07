"""The ``drain`` command and the auto-drain step (issue #352).

``corpus-rag drain [-p] [--approve]`` sends the study folder's pending
queue. ``auto_ingest: false`` needs ``--approve``. Every other command
first drains the folder queue when ``auto_ingest`` is on — except
``drain`` itself plus ``list``, ``provision``, ``migrate``, ``backup``,
and ``stop``. An auto-drain failure never blocks the command and never
prints, so it records into hook-errors.jsonl instead.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import io

import folder_state
import hook_state
import ingest_queue
import op_client
import op_runner
import study_commands
import study_registry
import study_server

AUTO_DRAIN_SKIP = frozenset({"drain", "list", "provision", "migrate", "backup", "stop"})


def _sender(cli: str, study: dict, inventory: dict) -> ingest_queue.Sender:
    base = study_server.base_url(study)

    def send(name: str, pairs: list[str]) -> dict:
        return op_runner.run_op(cli, name, pairs, base, inventory=inventory)

    return send


def _drain_queued(folder: folder_state.FolderState, slug_arg: str | None) -> tuple[str, dict]:
    """Shared drain body: resolve the study, send the folder's queue."""
    cli, slug, study = study_commands.scoped_study(slug_arg)
    inventory = op_runner.load_inventory(cli)

    def logger(path: str, route: str, result: str) -> None:
        ingest_queue.log_ingest(slug, path, route, result)

    report = ingest_queue.drain(
        folder.root, slug, sender=_sender(cli, study, inventory), logger=logger
    )
    return slug, report


def cmd_drain(args) -> int:
    """Send the folder's pending queue through the study server."""
    folder = folder_state.folder_state(scan=False)
    if folder.state != folder_state.CORPUS:
        print("No study folder here. Run drain inside a folder with .claude/corpus.json.")
        return 1
    if not folder.auto_ingest and not args.approve:
        print("auto_ingest is off for this study. Pass --approve to drain anyway.")
        return 1
    try:
        slug, report = _drain_queued(folder, args.study_slug)
    except (
        study_commands.CommandError,
        study_registry.RegistryError,
        op_runner.OpError,
        op_client.HttpError,
    ) as exc:
        print(f"not ok: {exc}")
        return 1
    print(
        f"Drain '{slug}': {report['ingested']} ingested, "
        f"{report['failed']} kept (failed), {report['dropped']} dropped."
    )
    return 0 if report["failed"] == 0 else 1


def auto_drain() -> None:
    """Drain the folder queue before a command; silent and fail-open."""
    try:
        folder = folder_state.folder_state(scan=False)
        if folder.state != folder_state.CORPUS or not folder.auto_ingest:
            return
        if not ingest_queue.queue_rows(folder.root):
            return
        with contextlib.redirect_stdout(io.StringIO()):
            _drain_queued(folder, None)
    except Exception as exc:  # noqa: BLE001 - a drain failure blocks nothing
        hook_state.record_error("auto_drain", repr(exc))

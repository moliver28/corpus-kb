"""Release CLI command bodies (pure move out of ``cli_release.py``).

The wired-store bodies behind assess, request, review, and status —
the same ``*_run`` idiom as ``execute_run``/``compare_run`` in
``cli_release_support``. ``cli_release`` parses flags and calls these;
the bodies are byte-identical moves for the 250-line rule.
"""

from __future__ import annotations

from corpus_kb.cli.cli_release_support import (
    _print_assess,
    _print_review_packet,
    _print_status,
    _tenant,
    _uuid_or_raise,
)


async def assess_run(
    codebook_version_id: str,
    profile: str | None,
    run_id: str | None,
    json_output: bool,
) -> int:
    """Compose every release gate over current evidence; WRITES NOTHING."""
    import json

    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.handlers import release_handlers

    async with wired_store() as wired:
        result = await release_handlers.assess_release(
            wired.store,
            _tenant(),
            _uuid_or_raise(codebook_version_id),
            profile_name=profile,
            run_id=_uuid_or_raise(run_id) if run_id else None,
        )
    payload = result.to_payload()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_assess(payload)
    return 0


async def request_run(
    codebook_version_id: str,
    creator: str,
    codebook_id: str | None,
    profile: str | None,
    json_output: bool,
) -> int:
    """Open a draft_candidate release pinned to the current content hash."""
    import json

    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.handlers import release_handlers
    from corpus_kb.research import guide_copy

    async with wired_store() as wired:
        result = await release_handlers.request_release(
            wired.store,
            _tenant(),
            _uuid_or_raise(codebook_version_id),
            creator,
            codebook_id=_uuid_or_raise(codebook_id) if codebook_id else None,
            profile_name=profile,
        )
    payload = result.to_payload()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(guide_copy.RELEASE_REQUEST_NOTE)
        print(
            f"requested {payload.get('release_id')} "
            f"(manifest {str(payload.get('manifest_sha256'))[:12]}..., "
            f"profile {payload.get('profile')})"
        )
        print(f"next: {guide_copy.RELEASE_NEXT_ASSESS}")
    return 0


async def review_run(codebook_version_id: str, release_id: str, json_output: bool) -> int:
    """Render the review packet: evidence, failures, waivers, next actions."""
    import json

    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.handlers import release_handlers

    async with wired_store() as wired:
        result = await release_handlers.review_release(
            wired.store,
            _tenant(),
            _uuid_or_raise(codebook_version_id),
            _uuid_or_raise(release_id),
        )
    payload = result.to_payload()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_review_packet(payload)
    return 0


async def status_run(codebook_version_id: str, json_output: bool) -> int:
    """Active release, draft state, pending proposals, the next command."""
    import json

    from corpus_kb.cli.cli_store import wired_store
    from corpus_kb.handlers import release_handlers

    async with wired_store() as wired:
        result = await release_handlers.status_report(
            wired.store,
            _tenant(),
            _uuid_or_raise(codebook_version_id),
        )
    payload = result.to_payload()
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_status(payload)
    return 0

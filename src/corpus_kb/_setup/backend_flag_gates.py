"""Typed refusals for the Postgres-only setup flags (issue #527, G15).

``setup --build-local`` and ``--skip-image-verify`` shape the Postgres
docker stack only; the DuckDB path (``setup_duckdb_cmd``) reads neither.
Both used to be silent no-ops under ``--backend duckdb``. One gate owns
the shape, so the next Postgres-only setup flag reuses it instead of
pasting a second refusal.
"""

from __future__ import annotations

from corpus_kb.errors import ConfigError


def refuse_pg_only_setup_flags(backend: str, *, build_local: bool, skip_image_verify: bool) -> None:
    """Refuse the Postgres-only setup flags on a DuckDB target.

    Raises one ConfigError naming the first set flag; the CLI maps it to
    the taxonomy's config exit (3). Postgres targets pass untouched.
    """
    if backend != "duckdb":
        return
    for flag, value in (
        ("--build-local", build_local),
        ("--skip-image-verify", skip_image_verify),
    ):
        if value:
            raise ConfigError(
                f"{flag} is a Postgres option. The target is DuckDB.",
                remediation="Drop the flag. Or point setup at Postgres.",
            )

"""Migration admin helpers for ``corpus-kb migrate`` (issue #348).

The three privileged knobs the migration runner shares with the CLI:
session settings for heavy migrations, the migration-025 embeddings
guard, and the superuser-to-app-role ownership handover. The runner
(``migrate_postgres.py``) calls them at the right moment; the CLI calls
``validate_session_settings`` before dispatch so a bad flag fails on
every backend.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import asyncpg

from corpus_kb._setup.migrate_admin_sql import owner_statement, schema_grant_statements
from corpus_kb.errors import ConfigError

_SESSION_SETTING_PATTERN = re.compile(r"^[A-Za-z0-9_.]+$")


def validate_session_settings(session_settings: dict[str, str] | None) -> None:
    """Refuse session settings that do not pass the strict allow-list.

    ``SET`` takes no bind parameters, so both the name and the value are
    checked before they reach the SQL text. Public so the CLI validates
    before dispatch (the DuckDB path never runs a SET).
    """
    for name, value in (session_settings or {}).items():
        if not _SESSION_SETTING_PATTERN.fullmatch(name) or not _SESSION_SETTING_PATTERN.fullmatch(
            value
        ):
            raise ConfigError(
                f"Invalid migration session setting {name}={value!r}. "
                "Use plain names and values like work_mem=1GB."
            )


async def apply_session_settings(
    conn: asyncpg.Connection, session_settings: dict[str, str] | None
) -> None:
    """Apply the requested session settings (work_mem and friends).

    ``SET`` takes no bind parameters, so the values ride the
    ``set_config`` function instead: the name and the value are bound,
    and the allow-list check still runs first (the CLI validates before
    dispatch). ``is_local`` false holds the setting for the whole
    session, exactly like ``SET``.
    """
    validate_session_settings(session_settings)
    for name, value in (session_settings or {}).items():
        await conn.execute("SELECT set_config($1, $2, false)", name, value)


async def refuse_025_with_embeddings(
    conn: asyncpg.Connection, pending: list[str], reset_embeddings: bool
) -> None:
    """The 0.6.0 guard for migration 025 (issue #348).

    Migration 025 retypes the main vector column and DISCARDS the old
    vectors (the model changed; converting them is meaningless). When the
    pre-025 ``chunks_vectors.vector`` column still holds embeddings and
    ``reset_embeddings`` is false, refuse with one clear line.
    """
    if reset_embeddings or not any(name.startswith("025_") for name in pending):
        return
    try:
        count = await conn.fetchval("SELECT count(*) FROM chunks_vectors WHERE vector IS NOT NULL")
    except asyncpg.PostgresError:
        # No old column or no table: nothing this guard protects.
        return
    if count:
        raise ConfigError(
            f"{count} stored embeddings live in the old vector column. "
            "Migration 025 discards them. "
            "Pass --reset-embeddings to accept, then re-embed the corpus."
        )


def dsn_role(dsn: str) -> str:
    """The user in a postgres DSN, or empty when it does not parse."""
    try:
        user = urlsplit(dsn).username
    except ValueError:
        return ""
    return unquote(user) if user else ""


# Object transfer for the superuser handover (issue #348). A blanket
# REASSIGN OWNED BY <superuser> is not safe: the bootstrap superuser also
# owns system-required catalog objects in the database, and Postgres
# refuses the whole statement (DependentObjectsStillExistError). Transfer
# only the objects in the app schemas instead; regclass/regprocedure text
# is already safely quoted for DDL.
_SU_OBJECTS_SQL = """
SELECT c.oid::regclass::text AS obj, c.relkind::text AS relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'corpus')
  AND c.relowner = $1
  AND c.relkind IN ('r', 'S', 'v', 'm')
ORDER BY 1
"""
_SU_FUNCTIONS_SQL = """
SELECT p.oid::regprocedure::text AS obj
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname IN ('public', 'corpus')
  AND p.proowner = $1
ORDER BY 1
"""
# One ALTER verb per pg_class relkind (each form takes `name OWNER TO`).
_OWNER_VERBS = {
    "r": "ALTER TABLE",
    "S": "ALTER SEQUENCE",
    "v": "ALTER VIEW",
    "m": "ALTER MATERIALIZED VIEW",
}


async def hand_over_to_app_role(
    conn: asyncpg.Connection, superuser_role: str, app_role: str
) -> None:
    """Move the migration objects to the app role and grant it access.

    Runs on the superuser connection right after the pending migrations
    (issue #348; 0.6.0 did this in plugin SQL): every table, sequence,
    view, materialized view and function the superuser owns in the app
    schemas changes owner to the app role, then the grants cover
    pre-existing objects and future default privileges.
    """
    if not app_role:
        return
    su_oid = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname = $1", superuser_role)
    app_oid = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname = $1", app_role)
    if su_oid and app_oid and su_oid != app_oid:
        for row in await conn.fetch(_SU_OBJECTS_SQL, su_oid):
            verb = _OWNER_VERBS[str(row["relkind"])]
            await conn.execute(owner_statement(verb, str(row["obj"]), app_role))
        for row in await conn.fetch(_SU_FUNCTIONS_SQL, su_oid):
            await conn.execute(owner_statement("ALTER FUNCTION", str(row["obj"]), app_role))
    for schema in ("public", "corpus"):
        for statement in schema_grant_statements(schema, app_role):
            await conn.execute(statement)

"""DSN probing and first-boot provisioning for local Postgres servers.

Used by ``corpus-kb quickstart``: classify why a DSN does not answer,
build the localhost:5432 fallback twin, and (with an explicit superuser
DSN) create the role and database exactly as the docker-compose stack
provisions them (``POSTGRES_USER`` becomes the database owner).

DSNs are URL-form (``postgresql://user:pass@host:port/db``) — the only
form this repo ships. Keyword-style DSNs are rejected with a clear
error instead of guessed at.

asyncpg 0.31 removed the private ``_parse_connstring`` helper the
installer once used; this module owns URL parsing from now on.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

import asyncpg

from corpus_kb.storage.sql_ident import sql_identifier


@dataclass
class DsnParts:
    """The five fields quickstart needs from a connection string."""

    user: str
    password: str
    host: str
    port: int
    database: str


@dataclass
class PgProbe:
    """One DSN probe outcome; the flags drive quickstart's resolution.

    ``database_missing`` means the SERVER answered but the named
    database does not exist; ``auth_failed`` means the role is missing
    or the password is wrong. Both are provisionable states.
    """

    ok: bool
    detail: str
    database_missing: bool = False
    auth_failed: bool = False


def parse_dsn(dsn: str) -> DsnParts:
    """Parse a URL-form postgres DSN; raise ValueError when unusable."""
    parsed = urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise ValueError(f"Not a postgresql:// URL DSN: {dsn!r}")
    return DsnParts(
        user=unquote(parsed.username or "corpus_user"),
        password=unquote(parsed.password or ""),
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=unquote(parsed.path.lstrip("/")) or "corpus_kb",
    )


def fallback_dsn(parts: DsnParts) -> str:
    """The localhost:5432 twin of a DSN (same credentials, same database)."""
    return (
        f"postgresql://{quote(parts.user, '')}:{quote(parts.password, '')}"
        f"@localhost:5432/{quote(parts.database, '')}"
    )


async def probe_dsn(dsn: str, timeout: int = 3) -> PgProbe:
    """Connect to a DSN and classify the failure when it does not answer."""
    try:
        conn = await asyncpg.connect(dsn, timeout=timeout)
    except asyncpg.exceptions.InvalidCatalogNameError as exc:
        return PgProbe(False, str(exc), database_missing=True)
    except asyncpg.exceptions.InvalidPasswordError as exc:
        return PgProbe(False, f"authentication failed: {exc}", auth_failed=True)
    except Exception as exc:  # connection refused, DNS, timeout, TLS, ...
        # Some drivers raise bare exceptions; the class name then tells
        # the user more than an empty string would.
        return PgProbe(False, str(exc) or type(exc).__name__)
    try:
        version = await conn.fetchval("SELECT version()")
    finally:
        await conn.close()
    return PgProbe(True, str(version))


async def ensure_role_and_database(superuser_dsn: str, parts: DsnParts) -> str:
    """Create the role and database when missing; return a report summary.

    Mirrors the compose-stack provisioning: CREATE ROLE ... LOGIN with
    the configured password, then CREATE DATABASE ... OWNER role. Never
    drops or alters existing objects. The migrations run AS the role
    afterwards, so ownership is the only grant they need.
    """
    role = _quote_ident(parts.user)
    conn = await asyncpg.connect(superuser_dsn, timeout=5)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", parts.user)
        if exists:
            report = f"role {parts.user} already exists"
        else:
            await conn.execute(
                f"CREATE ROLE {role} LOGIN PASSWORD {_quote_literal(parts.password)}"
            )
            report = f"created role {parts.user}"
        db_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", parts.database
        )
        if db_exists:
            report += f"; database {parts.database} already exists"
        else:
            statements = {
                "create_db": f"CREATE DATABASE {_quote_ident(parts.database)} OWNER {role}"
            }
            await conn.execute(statements["create_db"])
            report += f"; created database {parts.database} (owner {parts.user})"
    finally:
        await conn.close()
    return report


def _quote_ident(name: str) -> str:
    """Quote a DDL identifier after validating it needs no quoting."""
    return sql_identifier(name)


def _quote_literal(value: str) -> str:
    """Quote a DDL string literal (the CREATE ROLE password clause)."""
    return "'" + value.replace("'", "''") + "'"

"""The privileged migration statements (the ``migrate_admin`` seam).

``_setup/migrate_admin.py`` executes these; this module only composes
the text. The composition follows the ``storage/project_tables_sql.py``
idiom: the fragments carry no behavior and touch no database. The role
name comes from operator configuration and passes through
``quoted_identifier``; the object names come from the Postgres catalog's
own ``regclass``/``regprocedure`` text, which the server quotes for DDL.
"""

from __future__ import annotations

from corpus_kb.storage.sql_ident import quoted_identifier

__all__ = ["owner_statement", "schema_grant_statements"]


def owner_statement(verb: str, obj: str, role: str) -> str:
    """One ``ALTER ... OWNER TO`` statement for a catalog-reported object.

    ``verb`` comes from the closed relkind map, and ``obj`` is the
    ``regclass``/``regprocedure`` text the server already quotes for DDL;
    quoting it again would change the object identity.
    """
    return f"{verb} {obj} OWNER TO {quoted_identifier(role)}"


def schema_grant_statements(schema: str, role: str) -> tuple[str, ...]:
    """The five grants that open one app schema to the app role."""
    schema_q = quoted_identifier(schema)
    role_q = quoted_identifier(role)
    return (
        f"GRANT USAGE ON SCHEMA {schema_q} TO {role_q}",
        f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_q} TO {role_q}",
        f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema_q} TO {role_q}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_q} GRANT ALL ON TABLES TO {role_q}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_q} GRANT ALL ON SEQUENCES TO {role_q}",
    )

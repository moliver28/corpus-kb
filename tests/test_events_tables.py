"""F-10: event tables are created only when a probed table is missing."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_events_create_table_flag_skips_when_tables_present(monkeypatch):
    from corpus_kb.domain import events_tables as et

    async def all_present(conn_str, backend):
        return True

    monkeypatch.setattr(et, "events_tables_present", all_present)
    assert et.events_create_table_flag("postgresql://u:p@h:5432/db", "postgres") == "n"


@pytest.mark.asyncio
async def test_events_create_table_flag_fails_open_on_probe_error(monkeypatch):
    from corpus_kb.domain import events_tables as et

    async def broken(conn_str, backend):
        raise OSError("connection refused")

    monkeypatch.setattr(et, "events_tables_present", broken)
    assert et.events_create_table_flag("postgresql://u:p@h:5432/db", "postgres") == "y"


def test_postgres_env_leaves_create_table_to_the_flag():
    from corpus_kb.domain.application import _postgres_env

    env = _postgres_env("postgresql://u:p@h:5432/db")
    assert "CREATE_TABLE" not in env  # the static "y" line is gone; __init__ owns the flag


def test_events_table_names_match_application_prefix():
    from corpus_kb.domain import events_tables as et

    assert et.events_table_names() == (
        "corpusapplication_events",
        "corpusapplication_snapshots",
    )


def test_events_table_names_refuse_non_identifier_names(monkeypatch):
    """The probe f-string never carries a name outside ``[a-z_]+`` (RVW-03)."""
    from corpus_kb.domain import events_tables as et
    from corpus_kb.domain.application import CorpusApplication

    monkeypatch.setattr(CorpusApplication, "name", "corpus kb!")
    with pytest.raises(ValueError, match="Unsafe events table name"):
        et.events_table_names()
